"""
LegalMind Ingest Engine — PDF extraction, chunking, and vectorization.

Designed to be imported by the GUI or run standalone.  All progress is
reported through callbacks so any frontend (tkinter, Rich, plain print)
can display real-time status.

v9.0 — Streaming architecture: embeds per-file instead of accumulating
all chunks in RAM.  Subprocess windows suppressed on Windows.
"""

import gc
import io
import logging
import os
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass, field
from typing import Callable, Optional

# ── pythonw stdout/stderr fix ─────────────────────────────────────────────────
# pythonw.exe sets sys.stdout/stderr to None, crashing third-party libs that
# call sys.stdout.isatty() (transformers, tqdm, etc).  Must run before imports.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

warnings.filterwarnings("ignore")

# NOTE: Heavy imports (fitz, langchain) are deferred to function bodies
# so the GUI can open instantly.  Only stdlib + core are imported here.

from core import (
    BASE_DIR, get_embeddings,
    classify_doc_type, get_tesseract_cmd, get_poppler_path,
)
import store

# ── Logging setup ──────────────────────────────────────────────────────────
LOG_FILE = os.path.join(BASE_DIR, "legalmind.log")
logger = logging.getLogger("legalmind")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    # File handler — always captures DEBUG+
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)
    # Console handler (for debug bat) — INFO+
    # Only add if stdout exists (pythonw sets it to None)
    if sys.stdout is not None:
        ch = logging.StreamHandler(stream=sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(ch)

# ── OCR helpers (optional — graceful degradation) ────────────────────────────
_ocr_available = None
_ocr_status = ""  # human-readable status for the GUI


def check_ocr() -> bool:
    """Check whether Tesseract + Poppler + pdf2image are all available.

    Sets _ocr_status with a human-readable explanation of what's missing.
    """
    global _ocr_available, _ocr_status
    if _ocr_available is not None:
        return _ocr_available

    issues = []

    # 1. Check pytesseract module
    try:
        import pytesseract
    except ImportError:
        issues.append("pytesseract package not installed (pip install pytesseract)")

    # 2. Check Tesseract binary
    if not issues:
        tess_cmd = get_tesseract_cmd()
        pytesseract.pytesseract.tesseract_cmd = tess_cmd

        # First: does the binary file actually exist on disk?
        if not os.path.isfile(tess_cmd):
            # Maybe it's on PATH — check that too
            import shutil as _shutil
            if _shutil.which("tesseract"):
                # Found on PATH; pytesseract will find it automatically
                pass
            else:
                issues.append(
                    f"Tesseract binary not found at: {tess_cmd}\n"
                    "  Download: https://github.com/UB-Mannheim/tesseract/wiki"
                )
        else:
            # Binary exists — try calling it with a timeout
            try:
                # Suppress console window flash on Windows
                si = None
                cflags = 0
                if sys.platform == "win32":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = 0  # SW_HIDE
                    cflags = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(
                    [tess_cmd, "--version"],
                    capture_output=True, text=True, timeout=10,
                    startupinfo=si, creationflags=cflags,
                )
                if result.returncode != 0:
                    issues.append(f"Tesseract binary found but returned error: {tess_cmd}")
            except subprocess.TimeoutExpired:
                issues.append(f"Tesseract binary timed out (>10s): {tess_cmd}")
            except Exception as e:
                issues.append(f"Tesseract binary error: {e}")

    # 3. Check pdf2image module
    try:
        from pdf2image import convert_from_path
    except ImportError:
        issues.append("pdf2image package not installed (pip install pdf2image)")

    # 4. Check Poppler binaries
    poppler_path = get_poppler_path()
    poppler_ok = False
    if poppler_path and os.path.isdir(poppler_path):
        # Look for pdftoppm / pdftoppm.exe
        for name in ("pdftoppm", "pdftoppm.exe"):
            if os.path.isfile(os.path.join(poppler_path, name)):
                poppler_ok = True
                break
        if not poppler_ok:
            issues.append(
                f"Poppler directory exists but pdftoppm not found in: {poppler_path}"
            )
    else:
        # Check if pdftoppm is on system PATH (Linux / Homebrew installs)
        import shutil
        if shutil.which("pdftoppm"):
            poppler_ok = True
        else:
            issues.append(
                f"Poppler not found at: {poppler_path}\n"
                "  Download: https://github.com/oschwartz10612/poppler-windows/releases\n"
                "  Extract and set the path in Settings -> Poppler Path"
            )

    if issues:
        _ocr_available = False
        _ocr_status = "OCR unavailable:\n" + "\n".join(f"  • {i}" for i in issues)
        logger.info("OCR check: UNAVAILABLE -- %s", "; ".join(issues))
    else:
        _ocr_available = True
        _ocr_status = "OCR ready (Tesseract + Poppler)"
        logger.info("OCR check: READY (Tesseract + Poppler)")

    return _ocr_available


def get_ocr_status() -> str:
    """Return a human-readable OCR status string."""
    if _ocr_available is None:
        check_ocr()
    return _ocr_status


def _get_subprocess_kwargs():
    """Return kwargs to suppress console windows on Windows."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}


def ocr_page(pdf_path: str, page_num: int, dpi: int = 200) -> str:
    """OCR a single page of a PDF.  page_num is 1-based.

    Default DPI lowered from 300 to 200 to reduce memory usage
    while maintaining acceptable OCR quality for legal docs.
    """
    import pytesseract
    from pdf2image import convert_from_path

    # Configure Tesseract path
    pytesseract.pytesseract.tesseract_cmd = get_tesseract_cmd()

    # Suppress console window flashes on Windows (pytesseract + pdf2image)
    _patch_subprocess_for_hidden_windows()

    # Configure Poppler path
    poppler_path = get_poppler_path()
    kwargs = {"first_page": page_num, "last_page": page_num, "dpi": dpi}
    if poppler_path and os.path.isdir(poppler_path):
        kwargs["poppler_path"] = poppler_path

    images = convert_from_path(pdf_path, **kwargs)
    if images:
        text = pytesseract.image_to_string(images[0])
        # Free PIL image memory immediately
        del images
        gc.collect()
        return text
    return ""


_subprocess_patched = False

def _patch_subprocess_for_hidden_windows():
    """Monkey-patch subprocess.Popen globally to suppress console windows on Windows.

    This catches pytesseract, pdf2image/poppler, and any other subprocess calls
    that would otherwise flash a CMD window during ingest.
    """
    global _subprocess_patched
    if _subprocess_patched or sys.platform != "win32":
        return

    try:
        _OriginalPopen = subprocess.Popen

        class _SilentPopen(_OriginalPopen):
            def __init__(self, *args, **kwargs):
                if "startupinfo" not in kwargs or kwargs["startupinfo"] is None:
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = 0  # SW_HIDE
                    kwargs["startupinfo"] = si
                if "creationflags" not in kwargs:
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                super().__init__(*args, **kwargs)

        subprocess.Popen = _SilentPopen
        _subprocess_patched = True
        logger.debug("Patched subprocess.Popen to suppress console windows globally")
    except Exception as e:
        logger.debug("Could not patch subprocess.Popen: %s", e)


# ── Progress callback protocol ───────────────────────────────────────────────
@dataclass
class IngestProgress:
    """Snapshot of ingestion progress, passed to the callback."""
    phase: str                # "scanning" | "extracting" | "chunking" | "embedding" | "paused" | "done" | "error"
    message: str              # human-readable status line
    current_file: str = ""    # filename being processed
    file_index: int = 0       # 1-based index within the batch
    total_files: int = 0
    page_index: int = 0       # 1-based page within current file
    total_pages: int = 0
    chunks_so_far: int = 0
    elapsed: float = 0.0
    error: str = ""


# Default no-op callback
def _noop_cb(p: IngestProgress):
    pass


# ── Main ingestion logic ────────────────────────────────────────────────────
@dataclass
class IngestResult:
    """Summary returned after ingestion completes."""
    case_name: str
    files_processed: int = 0
    files_skipped: int = 0
    total_chunks: int = 0
    ocr_files: int = 0
    errors: list = field(default_factory=list)
    elapsed: float = 0.0


def discover_pdfs(folder: str) -> list[str]:
    """Recursively find all PDFs under *folder*."""
    pdfs = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(root, f))
    return sorted(pdfs)


def get_existing_sources(case_name: str,
                         progress_cb: Callable = _noop_cb) -> set[str]:
    """Return the set of source paths already fully indexed for a case.

    Raises store.StoreError if the index cannot be read. Callers MUST let
    that propagate rather than treating it as "nothing indexed".

    This function used to swallow every exception and return an empty set,
    which made a broken backend indistinguishable from a new case. On
    2026-07-21 that turned a missing chromadb module into
    "2525 new file(s) to process, 0 already indexed".

    Completion now comes from an explicit manifest, so a file interrupted
    mid-write has no row and is redone instead of being skipped forever.
    """
    progress_cb(IngestProgress(
        phase="scanning",
        message="Checking existing index...",
    ))
    sources = store.get_indexed_sources(case_name)
    logger.info("Diff: %d file(s) already complete for case '%s'.",
                len(sources), case_name)
    return sources


def extract_pdf_pages(pdf_path: str, use_ocr: bool = True,
                      progress_cb: Callable = _noop_cb,
                      file_index: int = 0, total_files: int = 0) -> list[dict]:
    """
    Extract text from each page of a PDF.
    Returns a list of dicts: [{"page": 1, "text": "..."}, ...]

    Uses PyMuPDF first; falls back to OCR per-page if digital text is thin.
    """
    pages = []
    fname = os.path.basename(pdf_path)

    import fitz  # PyMuPDF — lazy import for fast GUI startup
    logger.debug("Opening PDF: %s", fname)
    with fitz.open(pdf_path) as doc:
        total_pages = len(doc)
        logger.debug("  %s has %d page(s)", fname, total_pages)

        for page_num, page in enumerate(doc, start=1):
            progress_cb(IngestProgress(
                phase="extracting",
                message=f"Reading {fname} — page {page_num}/{total_pages}",
                current_file=fname,
                file_index=file_index,
                total_files=total_files,
                page_index=page_num,
                total_pages=total_pages,
            ))

            text = page.get_text("text") or ""
            text_len = len(text.strip())

            # Per-page OCR fallback. Two triggers:
            #   1. almost no digital text  -> scanned page
            #   2. plenty of text, but it is not readable -> broken font
            #      encoding. Without this second check such pages are silently
            #      indexed as gibberish, which is worse than missing: they
            #      pollute retrieval and look like real content.
            garbled = looks_garbled(text)
            if (text_len < 40 or garbled) and use_ocr and check_ocr():
                reason = "garbled text layer" if garbled else f"only {text_len} chars"
                logger.debug("  Page %d: %s — trying OCR...", page_num, reason)
                progress_cb(IngestProgress(
                    phase="extracting",
                    message=f"OCR scanning {fname} page {page_num}/{total_pages}...",
                    current_file=fname,
                    file_index=file_index,
                    total_files=total_files,
                    page_index=page_num,
                    total_pages=total_pages,
                ))
                try:
                    ocr_text = ocr_page(pdf_path, page_num)
                    # For a garbled page, ANY readable OCR output beats the
                    # existing text even if it is shorter -- length is not the
                    # test, readability is.
                    better = (len(ocr_text.strip()) > text_len if not garbled
                              else not looks_garbled(ocr_text))
                    if better:
                        text = ocr_text
                        logger.info("  Page %d: OCR recovered %d chars (was %d, %s)",
                                    page_num, len(ocr_text.strip()), text_len,
                                    "garbled" if garbled else "sparse")
                except Exception as e:
                    logger.warning("  Page %d: OCR failed: %s", page_num, e)

            if text.strip() and not looks_garbled(text):
                pages.append({"page": page_num, "text": text})
            elif text.strip():
                logger.warning("  %s p.%d: unreadable text layer and OCR could "
                               "not recover it — skipping rather than indexing "
                               "noise.", fname, page_num)

            # Yield GIL between pages so UI stays responsive
            time.sleep(0.005)

    logger.debug("  %s: extracted %d pages with content out of %d total", fname, len(pages), total_pages)
    return pages


# Detecting a broken text layer.
#
# When a PDF's ToUnicode map is broken, PyMuPDF emits raw glyph indices, which
# arrive as CONTROL CHARACTERS. Measured across this corpus, that is a clean
# discriminator -- corrupt pages run 32-97% control characters while every
# legitimate page measured runs exactly 0.0%:
#
#   corrupt  scanned exhibit, broken font map          ctrl 54.9%
#   corrupt  case-detail export                         ctrl 31.7%
#   corrupt  legacy payment-history print               ctrl 92.7%
#   LEGIT    transcript word index                   ctrl  0.0%  (digit 55.8%)
#   LEGIT    transcript prose                        ctrl  0.0%  (alpha 88.0%)
#   LEGIT    clean docket listing                    ctrl  0.0%  (alpha 78.4%)
#
# An earlier version tested the alphabetic ratio instead. That misfired badly:
# a transcript's word index ("$41,000 13:18 67:12") is only 27% alphabetic but
# entirely legitimate, and payment histories are similar. Since an unreadable
# page is now SKIPPED rather than indexed, a false positive silently deletes
# evidence -- so the test has to key on corruption, not on density.
GARBLED_CONTROL_RATIO = 0.05   # any real page measured 0.0%
GARBLED_ALNUM_RATIO = 0.30     # backstop: symbol soup with no control chars
MIN_CHARS_FOR_RATIO = 40


def looks_garbled(text: str) -> bool:
    """True if a text layer is present but is not plausibly readable text.

    Whitespace is excluded throughout: court forms are heavily padded, and
    counting the padding would drag legitimate pages below any ratio.
    """
    s = "".join(text.split())
    if len(s) < MIN_CHARS_FOR_RATIO:
        return False  # empty/near-empty is the other branch's problem

    n = len(s)
    if sum(1 for c in s if ord(c) < 32 or ord(c) == 127) / n > GARBLED_CONTROL_RATIO:
        return True
    alnum = sum(c.isalnum() for c in s) / n
    return alnum < GARBLED_ALNUM_RATIO


def ingest_folder(
    folder: str,
    case_name: str,
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
    use_ocr: bool = True,
    progress_cb: Callable = _noop_cb,
    cancel_flag: Optional[Callable[[], bool]] = None,  # return True to abort
    pause_flag: Optional[Callable[[], bool]] = None,   # return True while paused
    throttle=None,   # CpuThrottle or None -- paces the run to leave CPU free
) -> IngestResult:
    """
    Full ingestion pipeline:
      1. Discover PDFs
      2. Filter out already-indexed files
      3. Extract text (with per-page OCR fallback)
      4. Chunk with page-level metadata
      5. Embed and store in LanceDB

    progress_cb receives IngestProgress snapshots throughout.
    cancel_flag is polled between files -- return True to stop cleanly
                (all already-embedded chunks are preserved).
    pause_flag  is polled between files -- return True to pause.
                The loop sleeps until pause_flag returns False.
    throttle    if given, its priority is lowered up front and wait_for_slack()
                is called before each file so the run only uses spare CPU.
    """
    result = IngestResult(case_name=case_name)
    start = time.time()
    if throttle is not None:
        throttle.set_background_priority()

    # Suppress flashing CMD windows from subprocess calls (Windows only)
    _patch_subprocess_for_hidden_windows()

    logger.info("=== Ingest started: case=%s, folder=%s, chunk_size=%d ===",
                case_name, folder, chunk_size)

    # Phase 1: scan folder
    logger.info("Phase 1: Scanning folder for PDFs...")
    progress_cb(IngestProgress(phase="scanning", message=f"Scanning {folder} for PDFs..."))
    all_pdfs = discover_pdfs(folder)
    if not all_pdfs:
        logger.warning("No PDF files found in %s", folder)
        progress_cb(IngestProgress(phase="error", message="No PDF files found in folder.", error="No PDFs"))
        result.errors.append("No PDF files found in the selected folder.")
        return result

    logger.info("Found %d PDF(s) in folder", len(all_pdfs))
    for pdf in all_pdfs:
        logger.debug("  PDF: %s", os.path.basename(pdf))

    # Phase 2: diff against existing index
    logger.info("Phase 2: Checking existing index for case '%s'...", case_name)
    progress_cb(IngestProgress(
        phase="scanning",
        message=f"Found {len(all_pdfs)} PDF(s). Checking which are already indexed...",
        total_files=len(all_pdfs),
    ))
    existing = get_existing_sources(case_name, progress_cb=progress_cb)
    logger.info("Existing index has %d unique source paths", len(existing))
    new_pdfs = [p for p in all_pdfs if p not in existing]
    result.files_skipped = len(all_pdfs) - len(new_pdfs)

    if not new_pdfs:
        msg = f"All {len(all_pdfs)} files already indexed. Nothing new to do."
        logger.info(msg)
        progress_cb(IngestProgress(
            phase="done",
            message=msg,
            total_files=len(all_pdfs),
        ))
        result.elapsed = time.time() - start
        return result

    logger.info("%d new file(s) to process, %d already indexed", len(new_pdfs), result.files_skipped)
    progress_cb(IngestProgress(
        phase="scanning",
        message=f"{len(new_pdfs)} new file(s) to ingest ({result.files_skipped} already indexed).",
        total_files=len(new_pdfs),
    ))

    # Phase 3+4: extract, chunk, and embed each file IMMEDIATELY
    # (streaming architecture — never accumulates all chunks in RAM)
    logger.info("Phase 3: Loading text splitter (chunk_size=%d, overlap=%d)...", chunk_size, chunk_overlap)
    progress_cb(IngestProgress(
        phase="scanning",
        message="Loading text splitter...",
        total_files=len(new_pdfs),
    ))
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # lazy import
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )

    logger.info("Text splitter ready. Beginning streaming ingest of %d files "
                "into the LanceDB store...", len(new_pdfs))

    total_chunks = 0
    embed_buffer_chunks = []  # holds ONE file's chunks, never more
    embed_buffer_metas = []

    def _flush_embed_buffer():
        """Embed and store whatever is in the buffer, then clear it.

        The buffer holds exactly one file's chunks. It is NOT flushed on a
        size threshold mid-file: a crash between two partial flushes of the
        same file would leave that file half-indexed, and the diff seam
        would then treat it as done forever.
        """
        nonlocal total_chunks
        if not embed_buffer_chunks:
            return
        batch_count = len(embed_buffer_chunks)
        logger.info("  Embedding %d chunks (total so far: %d)...",
                    batch_count, total_chunks + batch_count)
        progress_cb(IngestProgress(
            phase="embedding",
            message=f"Embedding {batch_count} chunks "
                    f"(total: {total_chunks + batch_count})...",
            chunks_so_far=total_chunks + batch_count,
            total_files=len(new_pdfs),
        ))
        store.add_chunks(case_name, embed_buffer_chunks[:],
                         embed_buffer_metas[:])
        # Only count them once the write actually succeeded.
        total_chunks += batch_count
        embed_buffer_chunks.clear()
        embed_buffer_metas.clear()
        # Yield GIL so GUI stays responsive
        time.sleep(0.01)

    for i, pdf_path in enumerate(new_pdfs, start=1):
        # Yield to foreground work before starting this file. A file boundary
        # is the right place: we never pause mid-file, so a long backoff here
        # cannot leave a torn document.
        if throttle is not None:
            throttle.wait_for_slack()

        # Check stop (cancel)
        if cancel_flag and cancel_flag():
            # No flush needed: this runs at a file boundary, and every file
            # commits its own chunks before the next iteration begins.
            logger.info("Ingestion STOPPED by user at file %d/%d (chunks preserved: %d)",
                        i, len(new_pdfs), total_chunks)
            progress_cb(IngestProgress(
                phase="done",
                message=f"Stopped -- {result.files_processed} files, {total_chunks} chunks saved.",
                chunks_so_far=total_chunks,
            ))
            break

        # Check pause -- loop until unpaused (or stop is requested)
        if pause_flag and pause_flag():
            # Nothing buffered at a file boundary — see the cancel branch.
            logger.info("Ingestion PAUSED at file %d/%d (chunks preserved: %d)",
                        i, len(new_pdfs), total_chunks)
            progress_cb(IngestProgress(
                phase="paused",
                message=f"Paused at file {i}/{len(new_pdfs)} -- {total_chunks} chunks saved so far.",
                file_index=i,
                total_files=len(new_pdfs),
                chunks_so_far=total_chunks,
            ))
            while pause_flag and pause_flag():
                # Check if stop was requested while paused
                if cancel_flag and cancel_flag():
                    logger.info("Ingestion STOPPED while paused at file %d/%d", i, len(new_pdfs))
                    progress_cb(IngestProgress(
                        phase="done",
                        message=f"Stopped -- {result.files_processed} files, {total_chunks} chunks saved.",
                        chunks_so_far=total_chunks,
                    ))
                    result.total_chunks = total_chunks
                    result.elapsed = time.time() - start
                    return result
                time.sleep(0.2)
            logger.info("Ingestion RESUMED at file %d/%d", i, len(new_pdfs))
            progress_cb(IngestProgress(
                phase="extracting",
                message=f"Resumed at file {i}/{len(new_pdfs)}...",
                file_index=i,
                total_files=len(new_pdfs),
                chunks_so_far=total_chunks,
            ))

        fname = os.path.basename(pdf_path)
        doc_type = classify_doc_type(fname)

        logger.info("[File %d/%d] %s (type: %s)", i, len(new_pdfs), fname, doc_type)
        progress_cb(IngestProgress(
            phase="extracting",
            message=f"[{i}/{len(new_pdfs)}] Opening {fname}...",
            current_file=fname,
            file_index=i,
            total_files=len(new_pdfs),
        ))

        try:
            pages = extract_pdf_pages(
                pdf_path,
                use_ocr=use_ocr,
                progress_cb=progress_cb,
                file_index=i,
                total_files=len(new_pdfs),
            )

            if not pages:
                # Deliberately NOT marked ingested, so it is retried next
                # run. "No text extracted" is ambiguous — it can mean a
                # genuinely empty PDF, or that Tesseract was missing. The
                # fail-safe direction is to redo it rather than silently
                # drop a document from a legal-evidence corpus forever.
                msg = f"No text extracted from {fname} (0 pages with content)"
                logger.warning("%s — will be retried on the next run.", msg)
                result.errors.append(msg)
                progress_cb(IngestProgress(
                    phase="extracting",
                    message=msg,
                    current_file=fname,
                    file_index=i,
                    total_files=len(new_pdfs),
                    error=msg,
                ))
                continue

            file_chunks = 0
            for pg in pages:
                chunks = splitter.split_text(pg["text"])
                for chunk_text in chunks:
                    embed_buffer_chunks.append(chunk_text)
                    embed_buffer_metas.append({
                        "source": pdf_path,
                        "filename": fname,
                        "case": case_name,
                        "doc_type": doc_type,
                        "page": pg["page"],
                    })
                    file_chunks += 1

            # Free page text memory for this file
            del pages

            # Commit at the file boundary, then record completion. Reaching
            # here means extraction succeeded; a file that raised is handled
            # by the except block below and gets NO manifest row, so the next
            # run redoes it.
            _flush_embed_buffer()
            store.mark_file_ingested(case_name, pdf_path, file_chunks)

            result.files_processed += 1
            logger.info("  -> %d chunks from %s (total embedded: %d)",
                        file_chunks, fname, total_chunks + len(embed_buffer_chunks))
            progress_cb(IngestProgress(
                phase="extracting",
                message=f"[{i}/{len(new_pdfs)}] {fname}: {file_chunks} chunks",
                current_file=fname,
                file_index=i,
                total_files=len(new_pdfs),
                chunks_so_far=total_chunks + len(embed_buffer_chunks),
            ))

            # Periodic GC to reclaim memory from PyMuPDF/PIL objects
            if i % 25 == 0:
                gc.collect()
                logger.debug("  GC pass after file %d", i)

            # Yield GIL between files
            time.sleep(0.01)

        except Exception as e:
            # Discard this file's partial chunks. Carrying them forward would
            # fold them into the NEXT file's flush, and that file's manifest
            # row would then falsely cover content from this failed one.
            dropped = len(embed_buffer_chunks)
            embed_buffer_chunks.clear()
            embed_buffer_metas.clear()
            logger.error("Error processing %s: %s (dropped %d buffered chunk(s); "
                         "no manifest row written, so it will be retried)",
                         fname, e, dropped, exc_info=True)
            result.errors.append(f"Error processing {fname}: {e}")
            progress_cb(IngestProgress(
                phase="error",
                message=f"ERROR: {fname}: {e}",
                current_file=fname,
                file_index=i,
                total_files=len(new_pdfs),
                error=str(e),
            ))

    # Every file commits at its own boundary, so nothing should be left here.
    # Kept as a safety net; a non-empty buffer means a code path skipped its
    # commit and is a bug worth seeing in the log.
    if embed_buffer_chunks:
        logger.error("BUG: %d chunk(s) left unflushed after the file loop.",
                     len(embed_buffer_chunks))
        try:
            _flush_embed_buffer()
        except Exception as e:
            logger.error("Database write error on final flush: %s", e, exc_info=True)
            result.errors.append(f"Database write error: {e}")

    result.total_chunks = total_chunks

    # Final GC pass
    gc.collect()
    if throttle is not None:
        throttle.restore_priority()

    result.elapsed = time.time() - start
    logger.info("=== Ingest complete: %d files, %d skipped, %d chunks, %.1fs, %d errors ===",
                result.files_processed, result.files_skipped, result.total_chunks,
                result.elapsed, len(result.errors))
    progress_cb(IngestProgress(
        phase="done",
        message=(
            f"Done — {result.files_processed} files, {result.total_chunks} chunks "
            f"in {result.elapsed:.1f}s"
        ),
        total_files=len(new_pdfs),
        chunks_so_far=result.total_chunks,
        elapsed=result.elapsed,
    ))

    return result


# ── Extracted-text ingestion (fast path, no PDF rendering) ───────────────────

def strip_frontmatter(text: str) -> str:
    """Remove a leading YAML '--- ... ---' block if present.

    The canonical .txt files begin with metadata (status, source_relpath,
    text_sha1, ...) that must not be embedded as if it were document content.
    """
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl != -1:
                return text[nl + 1:].lstrip("\n")
    return text


def ingest_extracted(
    manifest_path: str,
    case_name: str,
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
    progress_cb: Callable = _noop_cb,
    cancel_flag: Optional[Callable[[], bool]] = None,
    throttle=None,
) -> IngestResult:
    """Ingest pre-extracted, de-duplicated canonical text from a manifest.

    This is the fast path: the text was already pulled out of the PDFs, so
    there is no PyMuPDF rendering and no OCR. Each canonical document is read,
    its frontmatter stripped, chunked and embedded.

    A canonical doc whose extracted text is ITSELF garbled (the extraction
    captured a broken font encoding) is skipped and recorded in
    result.errors, so a later pass can re-do it from the source PDF with OCR.
    It is deliberately NOT marked ingested, so that pass will pick it up.

    source_path is set to the canonical relpath -- stable, and already
    de-duplicated by the manifest, so re-running is idempotent and the
    A:/C: duplicate-file problem does not arise.
    """
    import json

    result = IngestResult(case_name=case_name)
    start = time.time()
    if throttle is not None:
        throttle.set_background_priority()

    base = os.path.dirname(os.path.abspath(manifest_path))
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    groups = manifest.get("groups", [])
    logger.info("=== Extracted-text ingest: case=%s, %d canonical docs ===",
                case_name, len(groups))

    already = store.get_indexed_sources(case_name)

    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap, length_function=len)

    total_chunks = 0
    for i, g in enumerate(groups, start=1):
        if cancel_flag and cancel_flag():
            logger.info("Extracted ingest STOPPED at %d/%d.", i, len(groups))
            break
        if throttle is not None:
            throttle.wait_for_slack()

        canon = g.get("canonical") or {}
        relpath = canon.get("relpath", "")
        out = canon.get("out", "")
        if not relpath or not out:
            continue
        if relpath in already:
            result.files_skipped += 1
            continue

        txt_path = os.path.join(base, out)
        fname = os.path.basename(relpath)
        progress_cb(IngestProgress(
            phase="extracting",
            message=f"[{i}/{len(groups)}] {fname}",
            current_file=fname, file_index=i, total_files=len(groups)))

        try:
            raw = open(txt_path, encoding="utf-8", errors="replace").read()
        except Exception as e:
            logger.warning("cannot read %s: %s", out, e)
            result.errors.append(f"unreadable extract: {relpath}")
            continue

        body = strip_frontmatter(raw)
        if looks_garbled(body):
            # Extraction captured broken glyphs. Leave it for a source-OCR
            # pass; do NOT mark it ingested.
            result.errors.append(f"garbled extract (needs OCR from source): {relpath}")
            logger.info("garbled extract, deferring to OCR: %s", relpath)
            continue

        chunks = splitter.split_text(body)
        if not chunks:
            continue

        doc_type = classify_doc_type(fname)
        metas = [{"source": relpath, "filename": fname, "case": case_name,
                  "doc_type": doc_type, "page": 0} for _ in chunks]
        try:
            store.add_chunks(case_name, chunks, metas)
            store.mark_file_ingested(case_name, relpath, len(chunks))
        except Exception as e:
            logger.error("store write failed for %s: %s", relpath, e, exc_info=True)
            result.errors.append(f"Error storing {relpath}: {e}")
            continue

        total_chunks += len(chunks)
        result.files_processed += 1
        if i % 50 == 0:
            gc.collect()

    result.total_chunks = total_chunks
    gc.collect()
    if throttle is not None:
        throttle.restore_priority()
    result.elapsed = time.time() - start
    logger.info("=== Extracted ingest complete: %d docs, %d skipped, %d chunks, "
                "%.1fs, %d deferred/errors ===",
                result.files_processed, result.files_skipped, result.total_chunks,
                result.elapsed, len(result.errors))
    progress_cb(IngestProgress(
        phase="done",
        message=(f"Done — {result.files_processed} docs, "
                 f"{result.total_chunks} chunks in {result.elapsed:.0f}s"),
        total_files=len(groups), chunks_so_far=result.total_chunks,
        elapsed=result.elapsed))
    return result


# ── Standalone CLI entry point ───────────────────────────────────────────────
def _cli_progress(p: IngestProgress):
    """Simple print-based progress for CLI usage."""
    prefix = {
        "scanning":   "🔍",
        "extracting": "📄",
        "chunking":   "✂️ ",
        "embedding":  "🧠",
        "done":       "✅",
        "error":      "❌",
    }.get(p.phase, "  ")
    print(f"  {prefix} {p.message}", flush=True)


if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog, simpledialog

    print("=" * 50)
    print("  LegalMind Ingest Engine (CLI)")
    print("=" * 50)

    root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
    case_name = simpledialog.askstring("Case Name", "Enter case name:")
    if not case_name:
        print("Cancelled."); sys.exit(0)

    folder = filedialog.askdirectory(title=f"Select PDF folder for '{case_name}'")
    root.destroy()
    if not folder:
        print("Cancelled."); sys.exit(0)

    result = ingest_folder(folder, case_name, progress_cb=_cli_progress)

    print(f"\n{'=' * 50}")
    print(f"  Case:       {result.case_name}")
    print(f"  Processed:  {result.files_processed} files")
    print(f"  Skipped:    {result.files_skipped} (already indexed)")
    print(f"  Chunks:     {result.total_chunks}")
    print(f"  Time:       {result.elapsed:.1f}s")
    if result.errors:
        print(f"  Errors:     {len(result.errors)}")
        for e in result.errors:
            print(f"    - {e}")
    print("=" * 50)
