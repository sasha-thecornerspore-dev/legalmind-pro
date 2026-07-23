import pytest

import store


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None
    yield


def _seed():
    texts = ["notice of pendency filed against the property",
             "the tenant signed a residential lease agreement",
             "affidavit of service upon the defendant"]
    metas = [
        {"source": "C:/d/1.pdf", "filename": "1.pdf", "case": "Case A",
         "doc_type": "Notice", "page": 1},
        {"source": "C:/d/2.pdf", "filename": "2.pdf", "case": "Case A",
         "doc_type": "Contract", "page": 2},
        {"source": "C:/d/3.pdf", "filename": "3.pdf", "case": "Case B",
         "doc_type": "Affidavit", "page": 3},
    ]
    store.add_chunks("Case A", texts, metas)


def test_returns_the_legacy_result_shape():
    _seed()
    hits = store.query_collection("Case A", "pendency", k=2)
    assert hits, "expected at least one hit"
    assert set(hits[0]) == {"content", "filename", "source", "page",
                            "doc_type", "case", "score", "distance"}


def test_scopes_results_to_the_requested_case():
    _seed()
    hits = store.query_collection("Case A", "affidavit service", k=10)
    assert all(h["case"] == "Case A" for h in hits)


def test_relevance_is_bounded_and_ordered():
    """Cosine distance -> 1-d, so score must sit in [0,1] and descend."""
    _seed()
    hits = store.query_collection("Case A", "notice of pendency", k=2)
    scores = [h["score"] for h in hits]
    assert all(0.0 <= s <= 1.0 for s in scores), scores
    assert scores == sorted(scores, reverse=True)


def test_semantically_close_query_beats_the_gate():
    _seed()
    hits = store.query_collection("Case A", "notice of pendency", k=1)
    assert hits[0]["score"] >= 0.10
    assert hits[0]["filename"] == "1.pdf"


def test_doc_type_filter_applies():
    _seed()
    hits = store.query_collection("Case A", "agreement", k=10,
                                  doc_type_filter="Contract")
    assert hits
    assert all(h["doc_type"] == "Contract" for h in hits)


def test_missing_table_returns_empty_not_error():
    assert store.query_collection("Nonexistent", "anything") == []


def test_query_raises_on_backend_failure():
    """Empty results must mean 'nothing matched', never 'index unreadable'.

    Answering "no documents found" because the database is broken is worse
    than raising in a legal-evidence tool: the user concludes the material
    does not exist.
    """
    def boom():
        raise OSError("disk gone")

    store._db = None
    original = store.connect
    store.connect = boom
    try:
        with pytest.raises(store.StoreError):
            store.query_collection("Case A", "anything")
    finally:
        store.connect = original


def test_query_matches_case_name_variants():
    _seed()
    hits = store.query_collection("  case   A  ", "pendency", k=5)
    assert hits, "whitespace/case variants must resolve to the same case"
