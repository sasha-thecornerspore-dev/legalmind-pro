"""Built-in Windows OCR (Windows.Media.Ocr) -- the OCR fallback when
Tesseract is not installed.

Windows 10/11 ship an OCR engine; nothing has to be downloaded. Pages are
rendered to images with PyMuPDF (already an app dependency, so Poppler is
not needed either) and read by a small PowerShell script that calls the
WinRT OCR API. One PowerShell launch handles any number of pages.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile

logger = logging.getLogger("legalmind.winocr")

# WinRT refuses images larger than this on either side.
_MAX_SIDE = 9000

_SCRIPT = r"""
param([switch]$Probe, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
        [Windows.Globalization.Language]::new("en-US"))
}
if ($Probe) {
    if ($engine) { "OCR-READY " + $engine.RecognizerLanguage.LanguageTag; exit 0 }
    "OCR-MISSING"; exit 3
}
if (-not $engine) { "OCR-MISSING"; exit 3 }

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $null = $t.Wait(-1)
    $t.Result
}

$i = 0
foreach ($p in $Paths) {
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        try {
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $text = ($result.Lines | ForEach-Object { $_.Text }) -join "`n"
        } finally { $stream.Dispose() }
        [pscustomobject]@{ i = $i; text = $text; error = "" } | ConvertTo-Json -Compress
    } catch {
        [pscustomobject]@{ i = $i; text = ""; error = "$_" } | ConvertTo-Json -Compress
    }
    $i++
}
"""

_script_path: str | None = None
_available: bool | None = None


def _powershell() -> str:
    found = shutil.which("powershell") or shutil.which("powershell.exe")
    if found:
        return found
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                        "WindowsPowerShell", "v1.0", "powershell.exe")


def _script() -> str:
    """Write the PowerShell script once per process, to a private temp folder."""
    global _script_path
    if _script_path and os.path.isfile(_script_path):
        return _script_path
    folder = tempfile.mkdtemp(prefix="legalmind_ocr_")
    path = os.path.join(folder, "winocr.ps1")
    with open(path, "w", encoding="utf-8-sig") as f:    # BOM: PS 5.1 reads UTF-8
        f.write(_SCRIPT)
    _script_path = path
    return path


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    kwargs = {}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0                                   # never flash a console
        kwargs = {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
    return subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-File", _script(), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, **kwargs)


def available() -> bool:
    """True if Windows' OCR engine can be used here (cached)."""
    global _available
    if _available is not None:
        return _available
    if sys.platform != "win32":
        _available = False
        return False
    try:
        r = _run(["-Probe"], timeout=90)
        _available = r.returncode == 0 and "OCR-READY" in (r.stdout or "")
        logger.info("Windows OCR probe: %s", (r.stdout or r.stderr or "").strip()[:200])
    except Exception as e:
        logger.info("Windows OCR probe failed: %s", e)
        _available = False
    return _available


def ocr_pdf_pages(pdf_path: str, pages: list[int], dpi: int = 300) -> dict[int, str]:
    """OCR the given 1-based pages of a PDF. Returns {page: text}.

    Raises RuntimeError if the engine fails on a page, so callers can tell
    "could not read" apart from "blank page".
    """
    import fitz
    if not pages:
        return {}
    tmp = tempfile.mkdtemp(prefix="legalmind_ocrpage_")
    try:
        pngs = []
        with fitz.open(pdf_path) as doc:
            for n in pages:
                page = doc[n - 1]
                zoom = dpi / 72.0
                side = max(page.rect.width, page.rect.height) * zoom
                if side > _MAX_SIDE:
                    zoom *= _MAX_SIDE / side
                png = os.path.join(tmp, f"p{n}.png")
                page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(png)
                pngs.append(png)
        r = _run(pngs, timeout=90 + 30 * len(pngs))
        records = {}
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rec = json.loads(line)
                    records[int(rec["i"])] = rec
                except (ValueError, KeyError):
                    continue
        out = {}
        for idx, n in enumerate(pages):
            rec = records.get(idx)
            if rec is None:
                raise RuntimeError(
                    f"Windows OCR returned nothing for page {n} "
                    f"(exit {r.returncode}): {(r.stderr or r.stdout or '').strip()[:300]}")
            if rec.get("error"):
                raise RuntimeError(f"Windows OCR failed on page {n}: {rec['error'][:300]}")
            out[n] = rec.get("text") or ""
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ocr_pdf_page(pdf_path: str, page_num: int) -> str:
    """OCR one 1-based page of a PDF."""
    return ocr_pdf_pages(pdf_path, [page_num])[page_num]
