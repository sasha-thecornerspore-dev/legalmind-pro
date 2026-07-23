import pytest

import store


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None
    yield


def test_new_case_returns_empty_set():
    """A genuinely new case is empty — this must NOT raise."""
    assert store.get_indexed_sources("Brand New Case") == set()


def test_returns_only_completed_files():
    store.mark_file_ingested("Case A", "C:/d/done.pdf", 5)
    assert store.get_indexed_sources("Case A") == {"C:/d/done.pdf"}


def test_scopes_to_the_case():
    store.mark_file_ingested("Case A", "C:/d/a.pdf", 1)
    store.mark_file_ingested("Case B", "C:/d/b.pdf", 1)
    assert store.get_indexed_sources("Case A") == {"C:/d/a.pdf"}


def test_backend_failure_raises_instead_of_returning_empty():
    """THE regression test. Returning set() here caused a full re-ingest."""
    def boom():
        raise OSError("disk gone")

    store._db = None
    original = store.connect
    store.connect = boom
    try:
        with pytest.raises(store.StoreError):
            store.get_indexed_sources("Case A")
    finally:
        store.connect = original


def test_torn_file_is_not_treated_as_complete():
    """Chunks written without a manifest row must be re-ingested.

    A previous file is marked complete FIRST so the manifest table really
    exists. Without that, get_indexed_sources returns set() via the
    'manifest absent' branch and this passes for the wrong reason — it
    would still pass under an implementation that infers completion from
    chunk presence, which is the exact bug being guarded against.
    """
    store.mark_file_ingested("Case A", "C:/d/done.pdf", 3)

    metas = [{"source": "C:/d/torn.pdf", "filename": "torn.pdf",
              "case": "Case A", "doc_type": "Motion", "page": 1}]
    store.add_chunks("Case A", ["partial content"], metas)
    # crash happened here — mark_file_ingested never ran for torn.pdf

    indexed = store.get_indexed_sources("Case A")
    assert indexed == {"C:/d/done.pdf"}, indexed
    assert "C:/d/torn.pdf" not in indexed


def test_case_name_variants_resolve_to_one_case():
    """Whitespace/case differences must not silently create a second case."""
    store.mark_file_ingested("Smith v Jones", "C:/d/a.pdf", 1)
    assert store.get_indexed_sources("  smith   V  JONES ") == {"C:/d/a.pdf"}


def test_list_cases_raises_on_backend_failure():
    """An empty case list must not be able to mean 'database broken'."""
    def boom():
        raise OSError("disk gone")

    store._db = None
    original = store.connect
    store.connect = boom
    try:
        with pytest.raises(store.StoreError):
            store.list_cases()
    finally:
        store.connect = original


def test_list_cases_shows_the_raw_name_not_the_key():
    metas = [{"source": "C:/d/a.pdf", "filename": "a.pdf",
              "case": "Smith v Jones", "doc_type": "Motion", "page": 1}]
    store.add_chunks("Smith v Jones", ["text"], metas)
    assert store.list_cases() == [{"name": "Smith v Jones", "chunks": 1}]


def test_list_cases_counts_chunks_per_case():
    metas_a = [{"source": "C:/d/a.pdf", "filename": "a.pdf", "case": "Case A",
                "doc_type": "Motion", "page": 1}] * 2
    metas_b = [{"source": "C:/d/b.pdf", "filename": "b.pdf", "case": "Case B",
                "doc_type": "Order", "page": 1}]
    store.add_chunks("Case A", ["one", "two"], metas_a)
    store.add_chunks("Case B", ["three"], metas_b)
    cases = {c["name"]: c["chunks"] for c in store.list_cases()}
    assert cases == {"Case A": 2, "Case B": 1}
