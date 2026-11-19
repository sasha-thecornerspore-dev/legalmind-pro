"""OCR when Tesseract is not installed, and pages that could not be read.

Before this, a machine without Tesseract + Poppler silently lost every scanned
page and logged-then-dropped every page with a broken text layer -- 382 pages
in one real docket folder, including affidavit pages that searches then
"could not find".
"""
import os
import sys

import pytest

import ingest_engine
import store
import winocr


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None
    monkeypatch.setattr(ingest_engine, "_ocr_available", None)
    monkeypatch.setattr(ingest_engine, "_ocr_backend", "")
    yield
    store._db = None


def _pdf(path, pages):
    """pages: list of str (a text page) or None (an image-only 'scan' page
    with no text layer, carrying the given caption as pixels)."""
    import fitz
    doc = fitz.open()
    for spec in pages:
        page = doc.new_page(width=612, height=792)
        if isinstance(spec, str):
            page.insert_text((72, 100), spec, fontsize=12)
        else:
            # Render text to an image and place only the image: no text layer.
            src = fitz.open()
            p = src.new_page(width=612, height=792)
            p.insert_text((72, 140), spec[1], fontsize=28)
            pix = p.get_pixmap(dpi=150)
            page.insert_image(page.rect, stream=pix.tobytes("png"))
    doc.save(str(path))
    return str(path)


def _no_tesseract(monkeypatch):
    monkeypatch.setattr(ingest_engine, "get_tesseract_cmd",
                        lambda: r"Z:\nope\tesseract.exe")
    import shutil
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda n, *a, **k: None if n in ("tesseract", "pdftoppm")
                        else real_which(n, *a, **k))


# ── backend selection ─────────────────────────────────────────────────────────

def test_falls_back_to_windows_ocr_without_tesseract(monkeypatch):
    _no_tesseract(monkeypatch)
    monkeypatch.setattr(winocr, "available", lambda: True)
    assert ingest_engine.check_ocr() is True
    assert ingest_engine._ocr_backend == "windows"
    assert "Windows" in ingest_engine.get_ocr_status()


def test_unavailable_when_neither_engine_exists(monkeypatch):
    _no_tesseract(monkeypatch)
    monkeypatch.setattr(winocr, "available", lambda: False)
    assert ingest_engine.check_ocr() is False
    assert ingest_engine._ocr_backend == ""


def test_ocr_page_routes_to_windows_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_engine, "_ocr_available", True)
    monkeypatch.setattr(ingest_engine, "_ocr_backend", "windows")
    seen = {}
    monkeypatch.setattr(winocr, "ocr_pdf_page",
                        lambda path, page: seen.update(p=(path, page)) or "TEXT")
    assert ingest_engine.ocr_page("x.pdf", 3) == "TEXT"
    assert seen["p"] == ("x.pdf", 3)


# ── pages that could not be read are reported, never silently dropped ────────

def test_unreadable_pages_are_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_engine, "check_ocr", lambda: False)
    pdf = _pdf(tmp_path / "a.pdf", ["the custodial agreement, section one: obligations of the custodian",
                                    (None, "SCANNED PAGE"),
                                    "page three has real words in it too, more than forty characters"])
    unread = []
    pages = ingest_engine.extract_pdf_pages(pdf, use_ocr=True, unread_out=unread)
    assert [p["page"] for p in pages] == [1, 3]
    assert unread == [2]


def test_blank_page_is_not_reported_once_ocr_has_looked(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_engine, "check_ocr", lambda: True)
    monkeypatch.setattr(ingest_engine, "ocr_page", lambda path, n: "")
    pdf = _pdf(tmp_path / "a.pdf", ["words on page one of the filing, long enough to count as text", (None, " ")])
    unread = []
    ingest_engine.extract_pdf_pages(pdf, use_ocr=True, unread_out=unread)
    assert unread == []


def test_ocr_failure_is_reported(monkeypatch, tmp_path):
    def boom(path, n):
        raise RuntimeError("engine crashed")
    monkeypatch.setattr(ingest_engine, "check_ocr", lambda: True)
    monkeypatch.setattr(ingest_engine, "ocr_page", boom)
    pdf = _pdf(tmp_path / "a.pdf", [(None, "SCANNED")])
    unread = []
    ingest_engine.extract_pdf_pages(pdf, use_ocr=True, unread_out=unread)
    assert unread == [1]


# ── the unread-pages ledger ───────────────────────────────────────────────────

def test_ledger_round_trip_and_case_ops(tmp_path):
    store.record_unread_pages("Case A", "C:/x/a.pdf", [8, 2])
    store.record_unread_pages("Case A", "C:/x/b.pdf", [1])
    assert store.unread_pages("Case A") == {"C:/x/a.pdf": [2, 8], "C:/x/b.pdf": [1]}
    store.record_unread_pages("Case A", "C:/x/b.pdf", [])        # now readable
    assert store.unread_pages("Case A") == {"C:/x/a.pdf": [2, 8]}
    assert store.unread_pages("Other") == {}
    store.rename_unread_pages("Case A", "Case B")
    assert store.unread_pages("Case A") == {}
    assert store.unread_pages("Case B") == {"C:/x/a.pdf": [2, 8]}
    store.drop_unread_pages("Case B")
    assert store.unread_pages("Case B") == {}


def test_ledger_lives_with_the_database(tmp_path):
    store.record_unread_pages("Case A", "C:/x/a.pdf", [1])
    assert os.path.isfile(os.path.join(store.VECTORS_DIR, store.UNREAD_PAGES_FILE))
    assert not store.looks_like_store(str(tmp_path / "nothing"))


# ── end to end: pages lost without OCR come back once OCR is available ──────

def test_unread_files_are_retried_when_ocr_becomes_available(monkeypatch, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    scan = _pdf(folder / "affidavit.pdf",
                ["affidavit of the custodian, page one, sworn before a notary public", (None, "PAGE TWO SCAN")])
    _pdf(folder / "plain.pdf", ["an ordinary motion with a proper text layer and enough words in it"])

    # Run 1: no OCR engine at all.
    monkeypatch.setattr(ingest_engine, "check_ocr", lambda: False)
    r1 = ingest_engine.ingest_folder(str(folder), "Case A", use_ocr=True)
    assert r1.unread_pages == 1
    assert store.unread_pages("Case A") == {scan: [2]}

    # Run 2: same folder, OCR now works -> only the affected file is redone.
    monkeypatch.setattr(ingest_engine, "check_ocr", lambda: True)
    monkeypatch.setattr(ingest_engine, "ocr_page",
                        lambda path, n: "custodial agreement table of contents")
    r2 = ingest_engine.ingest_folder(str(folder), "Case A", use_ocr=True)
    assert r2.files_retried == 1
    assert r2.files_processed == 1
    assert r2.unread_pages == 0
    assert store.unread_pages("Case A") == {}

    texts = [r["text"] for r in store.connect().open_table(store.CHUNKS_TABLE)
             .search().where("case_key = 'case_a'").select(["text", "source_path"])
             .limit(None).to_list()]
    assert any("custodial agreement" in t for t in texts)
    # No duplicates: the retried file's old chunks were replaced, not added to.
    assert sum("affidavit of the custodian" in t for t in texts) == 1


def test_no_retry_while_ocr_is_still_unavailable(monkeypatch, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    _pdf(folder / "a.pdf", [(None, "SCAN")])
    monkeypatch.setattr(ingest_engine, "check_ocr", lambda: False)
    ingest_engine.ingest_folder(str(folder), "Case A", use_ocr=True)
    r = ingest_engine.ingest_folder(str(folder), "Case A", use_ocr=True)
    assert r.files_retried == 0 and r.files_processed == 0


# ── the real Windows engine ───────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR only")
def test_windows_ocr_reads_a_scanned_page(tmp_path):
    if not winocr.available():
        pytest.skip("Windows OCR engine not available on this machine")
    pdf = _pdf(tmp_path / "scan.pdf", [(None, "CUSTODIAL AGREEMENT")])
    text = winocr.ocr_pdf_page(pdf, 1)
    assert "CUSTODIAL" in text.upper() and "AGREEMENT" in text.upper()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR only")
def test_windows_ocr_many_pages_in_one_call(tmp_path):
    if not winocr.available():
        pytest.skip("Windows OCR engine not available on this machine")
    pdf = _pdf(tmp_path / "scan.pdf", [(None, "FIRST PAGE"), (None, "SECOND PAGE")])
    out = winocr.ocr_pdf_pages(pdf, [1, 2])
    assert "FIRST" in out[1].upper() and "SECOND" in out[2].upper()
