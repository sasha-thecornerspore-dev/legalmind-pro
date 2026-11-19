"""Finding existing LegalMind databases on this machine (store.find_stores /
store.describe_store) -- what the GUI's "Find databases" window is built on."""
import os

import pytest

import store
from core import EMBED_DIM


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "unused"))
    store._db = None
    yield
    store._db = None


def _make_store(path, cases):
    """Create a real LegalMind store at `path` with {case: n_files} rows.

    Rows are written directly (zero vectors) so the test needs no embedding
    model."""
    store.set_vectors_dir(str(path), persist=False)
    rows = []
    for case, n in cases.items():
        for i in range(n):
            rows.append({
                "id": f"{case}-{i}", "source_path": f"C:/{case}/{i}.pdf",
                "filename": f"{i}.pdf", "case_name": case,
                "case_key": store.normalize_case(case), "doc_type": "Motion",
                "page": 1, "text": "t", "vector": [0.0] * EMBED_DIM,
            })
            store.mark_file_ingested(case, f"C:/{case}/{i}.pdf", 1)
    store.chunks_table().add(rows)
    store._db = None
    return os.path.abspath(str(path))


def _norm(paths):
    return sorted(os.path.normcase(os.path.abspath(p)) for p in paths)


def test_finds_every_store_and_nothing_else(tmp_path):
    root = tmp_path / "drive"
    a = _make_store(root / "Documents" / "legal-rag" / "vectors", {"Smith": 2})
    b = _make_store(root / "Old" / "LegalMind" / "vectors", {"Jones": 1})
    (root / "Empty" / "vectors").mkdir(parents=True)          # no tables
    (root / "lancedb" / "__manifest").mkdir(parents=True)     # manifest only

    found = store.find_stores([str(root)])
    assert _norm(found) == _norm([a, b])


def test_skips_noise_folders(tmp_path):
    root = tmp_path / "drive"
    keep = _make_store(root / "Cases" / "vectors", {"A": 1})
    _make_store(root / "proj" / "node_modules" / "pkg" / "vectors", {"B": 1})
    _make_store(root / ".cache" / "vectors", {"C": 1})
    _make_store(root / "$Recycle.Bin" / "S-1" / "vectors", {"D": 1})

    assert _norm(store.find_stores([str(root)])) == _norm([keep])


def test_respects_max_depth(tmp_path):
    root = tmp_path / "drive"
    deep = _make_store(root / "a" / "b" / "c" / "d" / "vectors", {"X": 1})
    assert store.find_stores([str(root)], max_depth=2) == []
    assert _norm(store.find_stores([str(root)], max_depth=5)) == _norm([deep])


def test_reports_progress_and_each_find(tmp_path):
    root = tmp_path / "drive"
    a = _make_store(root / "x" / "vectors", {"A": 1})
    found, progress = [], []
    store.find_stores([str(root)], on_found=found.append,
                      on_progress=lambda n, p: progress.append(n),
                      progress_every=1)
    assert _norm(found) == _norm([a])
    assert progress and progress == sorted(progress)


def test_can_be_stopped(tmp_path):
    root = tmp_path / "drive"
    _make_store(root / "x" / "vectors", {"A": 1})
    assert store.find_stores([str(root)], should_stop=lambda: True) == []


def test_missing_root_is_ignored(tmp_path):
    assert store.find_stores([str(tmp_path / "does-not-exist")]) == []


def test_describe_store_summarises_contents(tmp_path):
    p = _make_store(tmp_path / "vectors",
                    {"Foreclosure": 3, "custody files": 2})
    info = store.describe_store(p)
    assert info["error"] == ""
    assert info["is_legalmind"] is True
    assert info["chunks"] == 5
    assert info["files"] == 5
    assert info["cases"] == ["custody files", "Foreclosure"]   # case-insensitive
    assert info["updated"]                                     # a date string


def test_describe_store_flags_non_legalmind_lancedb(tmp_path):
    import lancedb
    import pyarrow as pa
    other = tmp_path / "other"
    lancedb.connect(str(other)).create_table(
        "embeddings", schema=pa.schema([pa.field("x", pa.int32())]))
    info = store.describe_store(str(other))
    assert info["is_legalmind"] is False
    assert info["chunks"] is None


def test_describe_store_does_not_switch_the_active_store(tmp_path):
    p = _make_store(tmp_path / "vectors", {"A": 1})
    store.set_vectors_dir(str(tmp_path / "active"), persist=False)
    store.describe_store(p)
    assert store.VECTORS_DIR == os.path.abspath(str(tmp_path / "active"))


def test_default_roots_exist():
    roots = store.default_search_roots()
    assert roots and all(os.path.isdir(r) for r in roots)


def test_overlapping_roots_are_walked_once(tmp_path):
    """Likely folders are searched first, then whole drives that contain
    them; the second pass must not walk (or report) them again."""
    root = tmp_path / "drive"
    docs = root / "Users" / "me" / "Documents"
    a = _make_store(docs / "legal-rag" / "vectors", {"A": 1})
    (root / "Other" / "x" / "y").mkdir(parents=True)

    def walked(roots):
        n = {}
        found = store.find_stores(roots, progress_every=1,
                                  on_progress=lambda c, p: n.update(c=c))
        return found, n["c"]

    found_once, n_once = walked([str(root)])
    found_both, n_both = walked([str(docs), str(root)])
    assert _norm(found_both) == _norm([a]) == _norm(found_once)
    assert n_both == n_once


def test_likely_folders_come_first():
    roots = store.default_search_roots()
    home = os.path.normcase(os.path.expanduser("~"))
    assert os.path.normcase(roots[0]) == home or any(
        os.path.normcase(r).startswith(home) for r in roots[:3])
