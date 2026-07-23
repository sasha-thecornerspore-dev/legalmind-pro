"""Ingest-pipeline correctness around crashes, resume and partial files."""
import pytest

import ingest_engine
import store


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None
    yield


def test_ingest_aborts_when_the_diff_seam_fails(monkeypatch, tmp_path):
    """A broken store must stop the run, not silently re-ingest everything.

    This is the regression guard for 2026-07-21, when a missing chromadb
    module produced "2525 new file(s) to process, 0 already indexed".
    """
    def boom(_case):
        raise store.StoreError("simulated backend failure")

    monkeypatch.setattr(store, "get_indexed_sources", boom)

    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(store.StoreError):
        ingest_engine.ingest_folder(str(folder), "Case A")


def test_completed_files_are_skipped_on_rerun(tmp_path):
    """The manifest is what makes resume work."""
    store.mark_file_ingested("Case A", str(tmp_path / "done.pdf"), 4)
    assert ingest_engine.get_existing_sources("Case A") == {
        str(tmp_path / "done.pdf")
    }


def test_failed_file_leaves_no_manifest_row(monkeypatch, tmp_path):
    """A file that raises mid-extraction must be retried, not skipped.

    Also guards the subtler bug: its partial chunks must not survive in the
    buffer to be folded into the NEXT file's commit, which would put them
    under that file's manifest row.
    """
    folder = tmp_path / "docs"
    folder.mkdir()
    bad = folder / "bad.pdf"
    good = folder / "good.pdf"
    bad.write_bytes(b"%PDF-1.4\n")
    good.write_bytes(b"%PDF-1.4\n")

    def fake_extract(pdf_path, **kwargs):
        if pdf_path.endswith("bad.pdf"):
            # Emitting pages then raising is the dangerous shape: chunks
            # reach the buffer before the failure.
            raise RuntimeError("simulated extraction failure")
        return [{"page": 1, "text": "the mortgage was assigned to the trust"}]

    monkeypatch.setattr(ingest_engine, "extract_pdf_pages", fake_extract)

    ingest_engine.ingest_folder(str(folder), "Case A", use_ocr=False)

    indexed = store.get_indexed_sources("Case A")
    assert str(good) in indexed, "the good file should be recorded"
    assert str(bad) not in indexed, "the failed file must be retried"


def test_partial_chunks_do_not_leak_into_the_next_file(monkeypatch, tmp_path):
    """A write failure must not leave chunks in the buffer.

    Failure here happens AFTER chunks are buffered, which is the dangerous
    shape: if the buffer is not cleared, those chunks ride along in the next
    file's flush and end up covered by that file's manifest row — silently
    attributing one document's content to another.
    """
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "aaa_bad.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "bbb_good.pdf").write_bytes(b"%PDF-1.4\n")

    def fake_extract(pdf_path, **kwargs):
        tag = "BAD" if "bad" in pdf_path else "GOOD"
        return [{"page": 1, "text": f"unique marker {tag} content"}]

    monkeypatch.setattr(ingest_engine, "extract_pdf_pages", fake_extract)

    real_add = store.add_chunks

    def flaky_add(case_name, texts, metas):
        if any("bad" in (m.get("source") or "") for m in metas):
            raise RuntimeError("simulated write failure")
        return real_add(case_name, texts, metas)

    monkeypatch.setattr(store, "add_chunks", flaky_add)

    ingest_engine.ingest_folder(str(folder), "Case A", use_ocr=False)

    stored = (store.chunks_table().search().select(["text"])
              .limit(None).to_arrow().column("text").to_pylist())

    assert not any("BAD" in t for t in stored), (
        f"failed file's chunks leaked into a later write: {stored}")
    assert any("GOOD" in t for t in stored), "the good file should still land"

    indexed = store.get_indexed_sources("Case A")
    assert str(folder / "aaa_bad.pdf") not in indexed
    assert str(folder / "bbb_good.pdf") in indexed


def test_chunks_are_attributed_to_the_right_file(monkeypatch, tmp_path):
    """Each file's chunks must carry that file's own source path."""
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "one.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "two.pdf").write_bytes(b"%PDF-1.4\n")

    def fake_extract(pdf_path, **kwargs):
        name = "one" if pdf_path.endswith("one.pdf") else "two"
        return [{"page": 1, "text": f"content belonging to {name}"}]

    monkeypatch.setattr(ingest_engine, "extract_pdf_pages", fake_extract)

    ingest_engine.ingest_folder(str(folder), "Case A", use_ocr=False)

    rows = (store.chunks_table().search().select(["source_path", "text"])
            .limit(None).to_arrow())
    pairs = dict(zip(rows.column("text").to_pylist(),
                     rows.column("source_path").to_pylist()))
    for text, src in pairs.items():
        expected = "one.pdf" if "one" in text else "two.pdf"
        assert src.endswith(expected), f"{text!r} attributed to {src}"
