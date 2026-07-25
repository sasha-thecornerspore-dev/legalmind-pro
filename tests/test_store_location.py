"""Configurable database location — connect a fresh install to an existing DB."""
import os

import pytest

import store


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "VECTORS_DIR", str(tmp_path / "vectors"))
    store._db = None
    yield


def test_set_vectors_dir_switches_target(tmp_path):
    a = str(tmp_path / "dbA")
    b = str(tmp_path / "dbB")
    store.set_vectors_dir(a, persist=False)
    store.add_chunks("Case A", ["alpha"],
                     [{"source": "a.pdf", "filename": "a.pdf", "case": "Case A",
                       "doc_type": "Motion", "page": 0}])
    assert store.VECTORS_DIR == os.path.abspath(a)

    # Point at a different, empty location — the first DB is not visible.
    store.set_vectors_dir(b, persist=False)
    assert store.list_cases() == []

    # Point back at the first — the data is there again.
    store.set_vectors_dir(a, persist=False)
    assert [c["name"] for c in store.list_cases()] == ["Case A"]


def test_env_var_overrides_default(monkeypatch, tmp_path):
    target = str(tmp_path / "envdb")
    monkeypatch.setenv("LEGALMIND_VECTORS", target)
    assert store._resolve_vectors_dir() == os.path.abspath(target)


def test_saved_setting_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEGALMIND_VECTORS", raising=False)
    monkeypatch.setattr(store, "_load_settings",
                        lambda: {"vectors_dir": str(tmp_path / "saved")})
    assert store._resolve_vectors_dir() == os.path.abspath(str(tmp_path / "saved"))


def test_looks_like_store_detects_real_db(tmp_path):
    assert not store.looks_like_store(str(tmp_path / "nope"))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not store.looks_like_store(str(empty))
    store.set_vectors_dir(str(tmp_path / "real"), persist=False)
    store.add_chunks("C", ["x"], [{"source": "x.pdf", "filename": "x.pdf",
                                   "case": "C", "doc_type": "Other", "page": 0}])
    assert store.looks_like_store(str(tmp_path / "real"))
