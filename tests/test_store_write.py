import os
import tempfile

import pytest

import store


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Point the store at a temp directory so tests never touch real data."""
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None  # reset the cached connection
    yield


def _metas(n, case="Test Case", src="C:/docs/a.pdf"):
    return [{"source": src, "filename": "a.pdf", "case": case,
             "doc_type": "Motion", "page": i + 1} for i in range(n)]


def test_add_chunks_returns_count_and_persists():
    n = store.add_chunks("Test Case", ["hello world", "second chunk"], _metas(2))
    assert n == 2
    assert store.chunks_table().count_rows() == 2


def test_every_column_is_populated():
    """tbl.add() silently null-fills missing columns — guard against it."""
    store.add_chunks("Test Case", ["hello"], _metas(1))
    row = store.chunks_table().search().limit(1).to_list()[0]
    for col in ("id", "source_path", "filename", "case_name", "doc_type",
                "page", "text"):
        assert row[col] is not None, f"{col} was null-filled"
    assert len(row["vector"]) == 384


def test_normalize_case_is_stable():
    assert store.normalize_case("Smith v Jones") == "smith_v_jones"
    assert store.normalize_case("  Smith  v  Jones ") == "smith_v_jones"


def test_case_name_with_illegal_table_chars_is_accepted():
    """Case is a column, not a table name — slashes must not break writes."""
    n = store.add_chunks("Smith/Jones", ["text"], _metas(1, case="Smith/Jones"))
    assert n == 1


def test_mark_file_ingested_records_the_file():
    store.mark_file_ingested("Test Case", "C:/docs/a.pdf", 7)
    rows = store.files_table().search().limit(None).to_list()
    assert len(rows) == 1
    assert rows[0]["source_path"] == "C:/docs/a.pdf"
    assert rows[0]["chunk_count"] == 7
