"""Case rename / clone / delete against the LanceDB store."""
import pytest

import store


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None
    yield


def _seed(case="Case A", n=2):
    metas = [{"source": f"C:/d/{i}.pdf", "filename": f"{i}.pdf", "case": case,
              "doc_type": "Motion", "page": i + 1} for i in range(n)]
    store.add_chunks(case, [f"document text {i}" for i in range(n)], metas)
    for i in range(n):
        store.mark_file_ingested(case, f"C:/d/{i}.pdf", 1)


def test_delete_removes_chunks_and_manifest():
    _seed()
    removed = store.delete_case("Case A")
    assert removed == 2
    assert store.list_cases() == []
    # The manifest must go too, or the next ingest skips those files while
    # their chunks no longer exist.
    assert store.get_indexed_sources("Case A") == set()


def test_delete_leaves_other_cases_alone():
    _seed("Case A")
    _seed("Case B")
    store.delete_case("Case A")
    assert [c["name"] for c in store.list_cases()] == ["Case B"]
    assert len(store.get_indexed_sources("Case B")) == 2


def test_rename_moves_chunks_and_manifest():
    _seed("Old Name")
    affected = store.rename_case("Old Name", "New Name")
    assert affected == 2
    assert [c["name"] for c in store.list_cases()] == ["New Name"]
    assert len(store.get_indexed_sources("New Name")) == 2
    assert store.get_indexed_sources("Old Name") == set()


def test_rename_onto_existing_case_is_refused():
    _seed("Case A")
    _seed("Case B")
    with pytest.raises(store.StoreError):
        store.rename_case("Case A", "Case B")


def test_clone_copies_without_touching_the_source():
    _seed("Case A")
    copied = store.clone_case("Case A", "Case A Copy")
    assert copied == 2
    names = sorted(c["name"] for c in store.list_cases())
    assert names == ["Case A", "Case A Copy"]
    assert len(store.get_indexed_sources("Case A")) == 2
    assert len(store.get_indexed_sources("Case A Copy")) == 2


def test_clone_is_searchable_and_keeps_vectors():
    _seed("Case A")
    store.clone_case("Case A", "Case A Copy")
    src = store.query_collection("Case A", "document text", k=1)
    dst = store.query_collection("Case A Copy", "document text", k=1)
    assert src and dst
    # Vectors were copied, not re-embedded, so scores must match exactly.
    assert src[0]["distance"] == pytest.approx(dst[0]["distance"])


def test_clone_onto_existing_case_is_refused():
    _seed("Case A")
    _seed("Case B")
    with pytest.raises(store.StoreError):
        store.clone_case("Case A", "Case B")


def test_forget_file_removes_chunks_and_manifest():
    _seed("Case A", n=3)
    target = "C:/d/1.pdf"
    assert target in store.get_indexed_sources("Case A")

    removed = store.forget_file("Case A", target)
    assert removed == 1
    # Both must go, or the file is skipped forever while its chunks are gone.
    assert target not in store.get_indexed_sources("Case A")
    rows = (store.chunks_table().search().select(["source_path"])
            .limit(None).to_arrow().column("source_path").to_pylist())
    assert target not in rows
    assert len(rows) == 2, "other files must be untouched"


def test_forget_file_leaves_other_cases_alone():
    _seed("Case A", n=2)
    _seed("Case B", n=2)
    store.forget_file("Case A", "C:/d/0.pdf")
    assert len(store.get_indexed_sources("Case B")) == 2


def test_copy_subset_carves_out_a_case():
    metas_dk = [{"source": f"C:/dump/docket/{i}.pdf", "filename": f"{i}.pdf",
                 "case": "Foreclosure", "doc_type": "Motion", "page": 0}
                for i in range(3)]
    metas_ot = [{"source": "C:/other/note.pdf", "filename": "note.pdf",
                 "case": "Foreclosure", "doc_type": "Other", "page": 0}]
    store.add_chunks("Foreclosure", ["docket one", "docket two", "docket three"], metas_dk)
    store.add_chunks("Foreclosure", ["a note"], metas_ot)
    for m in metas_dk:
        store.mark_file_ingested("Foreclosure", m["source"], 1)

    n = store.copy_subset("Foreclosure", "Docket", "docket")
    assert n == 3
    cases = {c["name"]: c["chunks"] for c in store.list_cases()}
    assert cases == {"Foreclosure": 4, "Docket": 3}
    # manifest carried across
    assert len(store.get_indexed_sources("Docket")) == 3
    # source case untouched
    assert len(store.get_indexed_sources("Foreclosure")) == 3


def test_copy_subset_refuses_existing_destination():
    store.add_chunks("Foreclosure", ["x"],
                     [{"source": "C:/dump/docket/a.pdf", "filename": "a.pdf",
                       "case": "Foreclosure", "doc_type": "Motion", "page": 0}])
    store.add_chunks("Docket", ["y"],
                     [{"source": "C:/dump/docket/b.pdf", "filename": "b.pdf",
                       "case": "Docket", "doc_type": "Motion", "page": 0}])
    import pytest
    with pytest.raises(store.StoreError):
        store.copy_subset("Foreclosure", "Docket", "docket")
