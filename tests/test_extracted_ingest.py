"""Extracted-text ingestion from a dedup manifest."""
import json
import os

import pytest

import ingest_engine
import store


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None
    yield


def test_strip_frontmatter():
    t = ("---\nstatus: canonical\nsource_relpath: a/b.pdf\n---\n\n"
         "IN THE CIRCUIT COURT the body begins here.")
    assert ingest_engine.strip_frontmatter(t).startswith("IN THE CIRCUIT COURT")


def test_strip_frontmatter_noop_when_absent():
    t = "No header, just text."
    assert ingest_engine.strip_frontmatter(t) == t


def _manifest(tmp_path, docs):
    """docs: list of (relpath, body). Writes .txt files + a manifest.json."""
    base = tmp_path / "corpus"
    (base / "canonical").mkdir(parents=True)
    groups = []
    for i, (rel, body) in enumerate(docs):
        out = f"canonical/doc{i}.txt"
        (base / out).write_text(
            f"---\nsource_relpath: {rel}\ntext_sha1: x\n---\n\n{body}",
            encoding="utf-8")
        groups.append({"key": rel, "canonical": {"relpath": rel, "out": out,
                                                 "hash": "x"}, "superseded": []})
    mp = base / "manifest.json"
    mp.write_text(json.dumps({"groups": groups}), encoding="utf-8")
    return str(mp)


def test_clean_docs_are_ingested(tmp_path):
    mp = _manifest(tmp_path, [
        ("03_Motions/dismiss.pdf",
         "Defendant moves to dismiss the foreclosure complaint for lack of "
         "standing, the plaintiff never held the note."),
        ("notes/history.pdf",
         "The payment history shows the loan was current through April 2010."),
    ])
    r = ingest_engine.ingest_extracted(mp, "Foreclosure")
    assert r.files_processed == 2
    assert r.total_chunks >= 2
    assert store.get_indexed_sources("Foreclosure") == {
        "03_Motions/dismiss.pdf", "notes/history.pdf"}


def test_garbled_extract_is_deferred_not_indexed(tmp_path):
    mp = _manifest(tmp_path, [
        ("good.pdf", "A perfectly readable motion to compel discovery responses."),
        ("bad.pdf", "".join(chr(i % 20) for i in range(400))),  # control-char soup
    ])
    r = ingest_engine.ingest_extracted(mp, "Foreclosure")
    assert r.files_processed == 1
    assert "bad.pdf" not in store.get_indexed_sources("Foreclosure")
    assert any("bad.pdf" in e for e in r.errors)


def test_rerun_skips_completed_docs(tmp_path):
    mp = _manifest(tmp_path, [
        ("a.pdf", "Notice of pendency filed against the subject property."),
    ])
    ingest_engine.ingest_extracted(mp, "Foreclosure")
    r2 = ingest_engine.ingest_extracted(mp, "Foreclosure")
    assert r2.files_processed == 0 and r2.files_skipped == 1


def test_ingested_text_is_searchable(tmp_path):
    mp = _manifest(tmp_path, [
        ("assignment.pdf",
         "The deed of trust was assigned by Acme Bank to the securitization "
         "trust, recorded in the county land records."),
    ])
    ingest_engine.ingest_extracted(mp, "Foreclosure")
    hits = store.query_collection("Foreclosure", "acme bank assignment of deed", k=1)
    assert hits and hits[0]["score"] > 0.35
    assert hits[0]["source"] == "assignment.pdf"
