"""LanceDB vector store for LegalMind Pro.

This is the ONLY module that talks to the vector database. Everything else
goes through the functions here.

IMPORTANT: the data directory is `vectors/`, never `lancedb/`. A directory
named `lancedb` in the repo root shadows the real library as an empty
namespace package, because the app runs with the repo as its cwd.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import uuid

import pyarrow as pa

from core import BASE_DIR, EMBED_DIM, get_embeddings, _load_settings, save_settings

logger = logging.getLogger("legalmind.store")

# Where the LanceDB store lives. Resolution order, most specific first:
#   1. the LEGALMIND_VECTORS environment variable
#   2. a saved "vectors_dir" setting (set in the GUI's Settings tab)
#   3. the default <install-dir>/vectors
# This is what lets a fresh install point at an existing database instead of
# creating a new empty one next to the code.
DEFAULT_VECTORS_DIR = os.path.join(BASE_DIR, "vectors")


def _resolve_vectors_dir() -> str:
    env = os.environ.get("LEGALMIND_VECTORS")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    saved = (_load_settings() or {}).get("vectors_dir")
    if saved:
        return os.path.abspath(os.path.expanduser(saved))
    return DEFAULT_VECTORS_DIR


VECTORS_DIR = _resolve_vectors_dir()

CHUNKS_TABLE = "chunks"
FILES_TABLE = "ingested_files"

_db = None


def set_vectors_dir(path: str, *, persist: bool = True) -> None:
    """Point the store at a different database directory.

    Drops the cached connection so the next call opens the new location.
    With persist=True the choice is saved so it survives a restart -- this is
    how a fresh install is told where an existing database lives.
    """
    global VECTORS_DIR, _db
    VECTORS_DIR = os.path.abspath(os.path.expanduser(path))
    _db = None
    if persist:
        save_settings({"vectors_dir": VECTORS_DIR})
    logger.info("Vector store location set to %s", VECTORS_DIR)


def looks_like_store(path: str) -> bool:
    """True if `path` holds an existing LanceDB store with real tables.

    Keys on '*.lance' table directories only. NOT on '__manifest', which
    LanceDB creates the instant you connect to a folder — so checking for it
    would flag an empty, freshly-connected directory as if it held data.
    """
    try:
        return os.path.isdir(path) and any(
            e.endswith(".lance") for e in os.listdir(path))
    except Exception:
        return False


# ── Finding existing databases on this machine ──────────────────────────────
# A fresh install (or an unzipped new release) starts with its own empty
# `vectors/` folder, so the user's real cases seem to have vanished. These
# helpers search the computer for existing stores so the GUI can offer to
# reconnect one. They only read; nothing is moved or copied.

# Folder names never worth descending into: OS/system areas, package caches
# and build output. Compared case-insensitively. Names starting with "." or
# "$" (hidden folders, $Recycle.Bin, $WinREAgent ...) are skipped as well.
_SEARCH_SKIP_DIRS = {
    "windows", "program files", "program files (x86)", "programdata",
    "appdata", "system volume information", "recovery", "perflogs",
    "msocache", "intel", "amd", "nvidia",
    "node_modules", "__pycache__", "site-packages", "dist-packages",
    "venv", "env", "build", "dist", "target",
    "library", "system", "applications", "proc", "sys", "dev", "usr",
    "bin", "sbin", "etc", "var", "opt", "snap", "boot", "lib", "lib64",
}


def _windows_user_folders() -> list[str]:
    """Documents / Desktop / Downloads as Windows has them -- often moved to
    another drive or into OneDrive, so ~/Documents is not enough."""
    out = []
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        for name in ("Personal", "Desktop",
                     "{374DE290-123F-4565-9164-39C4925E467B}"):   # Downloads
            try:
                out.append(os.path.expandvars(winreg.QueryValueEx(key, name)[0]))
            except OSError:
                pass
    except Exception:
        pass
    return out


def default_search_roots() -> list[str]:
    """Where to look for existing stores, most likely places first so a
    store in the user's own folders shows up within seconds:

      1. the home folder, the (possibly relocated) Documents/Desktop/Downloads,
         `<drive>:\\Users\\<name>` on every drive, and the folder holding this
         install;
      2. then every fixed drive (Windows) or mounted volumes (macOS/Linux).

    find_stores() never walks a folder twice, so the overlap is free.
    """
    likely = [os.path.expanduser("~")]
    drives: list[str] = []
    if os.name == "nt":
        import ctypes
        import string
        drive_fixed = 3
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            try:
                is_fixed = ctypes.windll.kernel32.GetDriveTypeW(root) == drive_fixed
            except Exception:
                is_fixed = os.path.isdir(root)
            if is_fixed and os.path.isdir(root):
                drives.append(root)
        likely += _windows_user_folders()
        user = os.path.basename(os.path.expanduser("~"))
        likely += [os.path.join(d, "Users", user) for d in drives]
    else:
        drives += [p for p in ("/Volumes", "/mnt", "/media") if os.path.isdir(p)]
    likely.append(os.path.dirname(os.path.dirname(os.path.abspath(BASE_DIR))))

    roots: list[str] = []
    seen: set[str] = set()
    for p in likely + drives:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen and os.path.isdir(p):
            seen.add(key)
            roots.append(p)
    return roots


def find_stores(roots: list[str] | None = None, *, max_depth: int = 8,
                on_found=None, on_progress=None, should_stop=None,
                progress_every: int = 250) -> list[str]:
    """Walk `roots` (default: default_search_roots()) and return every folder
    that looks_like_store().

    - Never descends into a store, nor into '*.lance' table folders (a big
      store holds tens of thousands of files), symlinks or junctions.
    - on_found(path) is called as each store is found; on_progress(n, path)
      every `progress_every` folders; should_stop() is polled so a GUI can
      cancel. Unreadable folders are skipped silently.
    """
    if roots is None:
        roots = default_search_roots()
    found: list[str] = []
    seen: set[str] = set()       # stores already reported
    visited: set[str] = set()    # folders already walked (roots may overlap)
    stack = [(os.path.abspath(r), 0) for r in reversed(roots)]
    checked = 0
    while stack:
        if should_stop and should_stop():
            break
        path, depth = stack.pop()
        key = os.path.normcase(path)
        if key in visited:
            continue
        visited.add(key)
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            continue
        checked += 1
        if on_progress and checked % progress_every == 0:
            try:
                on_progress(checked, path)
            except Exception:
                pass

        if any(e.name.endswith(".lance") for e in entries):
            key = os.path.normcase(os.path.realpath(path))
            if key not in seen:
                seen.add(key)
                found.append(path)
                logger.info("Found a LanceDB store at %s", path)
                if on_found:
                    try:
                        on_found(path)
                    except Exception:
                        pass
            continue        # never look inside a store

        if depth >= max_depth:
            continue
        subdirs = []
        for e in entries:
            name = e.name
            if (name.startswith((".", "$")) or name.endswith(".lance")
                    or name.lower() in _SEARCH_SKIP_DIRS):
                continue
            try:
                if not e.is_dir(follow_symlinks=False) or e.is_symlink():
                    continue
                if getattr(e, "is_junction", lambda: False)():
                    continue
            except OSError:
                continue
            subdirs.append(e.path)
        # Reverse so folders are visited in alphabetical order.
        for sub in sorted(subdirs, key=str.lower, reverse=True):
            stack.append((sub, depth + 1))
    if on_progress:
        try:
            on_progress(checked, "")
        except Exception:
            pass
    return found


def describe_store(path: str) -> dict:
    """Summarise a store WITHOUT making it the active one.

    Returns {path, is_legalmind, chunks, files, cases, updated, error}.
    Case names come from the small ingest manifest, not the (possibly huge)
    chunks table, so this stays quick even for a multi-gigabyte store.
    """
    info = {"path": os.path.abspath(path), "is_legalmind": False,
            "chunks": None, "files": None, "cases": [], "updated": "",
            "error": ""}
    try:
        import lancedb
        db = lancedb.connect(info["path"])     # separate from the cached _db
        names = db.list_tables().tables
        info["is_legalmind"] = CHUNKS_TABLE in names
        if CHUNKS_TABLE in names:
            info["chunks"] = db.open_table(CHUNKS_TABLE).count_rows()
        if FILES_TABLE in names:
            tbl = db.open_table(FILES_TABLE)
            info["files"] = tbl.count_rows()
            arr = (tbl.search().select(["case_name", "case_key"])
                      .limit(None).to_arrow())
            by_key: dict[str, str] = {}
            for raw, key in zip(arr.column("case_name").to_pylist(),
                                arr.column("case_key").to_pylist()):
                by_key.setdefault(key, raw)
            info["cases"] = sorted(by_key.values(), key=lambda s: (s or "").lower())
        # Each write adds a file under <table>.lance/_versions, so that
        # folder's mtime is the store's last-updated time.
        stamps = []
        for name in os.listdir(info["path"]):
            if name.endswith(".lance"):
                versions = os.path.join(info["path"], name, "_versions")
                target = versions if os.path.isdir(versions) else os.path.join(
                    info["path"], name)
                stamps.append(os.path.getmtime(target))
        if stamps:
            info["updated"] = _dt.datetime.fromtimestamp(max(stamps)).strftime(
                "%Y-%m-%d %H:%M")
    except Exception as e:
        info["error"] = str(e)
    return info


class StoreError(RuntimeError):
    """Raised when the vector store is unreachable or inconsistent.

    Callers must NOT treat this as 'no data'. See get_indexed_sources().
    """


def normalize_case(case_name: str) -> str:
    """Canonical matching key for a case name. The single source of truth.

    Previously duplicated as `case_name.lower().replace(" ", "_")` in 11
    places across the codebase.

    Rows store BOTH the raw name (for display) and this key (for matching),
    so "Smith v Jones" and "Smith  v  Jones" resolve to one case
    without the UI having to show a mangled name back to the user.
    """
    return "_".join(case_name.lower().split())


CHUNKS_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("source_path", pa.string()),
    pa.field("filename", pa.string()),
    pa.field("case_name", pa.string()),   # raw, as the user typed it
    pa.field("case_key", pa.string()),    # normalize_case(case_name)
    pa.field("doc_type", pa.string()),
    pa.field("page", pa.int32()),
    pa.field("text", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
])

FILES_SCHEMA = pa.schema([
    pa.field("source_path", pa.string()),
    pa.field("case_name", pa.string()),
    pa.field("case_key", pa.string()),
    pa.field("chunk_count", pa.int32()),
    pa.field("completed_at", pa.string()),
])


def connect():
    """Return a cached LanceDB connection, creating the directory if needed."""
    global _db
    if _db is None:
        import lancedb
        if not hasattr(lancedb, "connect"):
            raise StoreError(
                "The 'lancedb' name resolved to a namespace package, not the "
                "library. A directory named 'lancedb' is shadowing it — the "
                "data directory must be named 'vectors'."
            )
        os.makedirs(VECTORS_DIR, exist_ok=True)
        _db = lancedb.connect(VECTORS_DIR)
    return _db


def chunks_table():
    """Open the chunks table, creating it on first use."""
    return connect().create_table(CHUNKS_TABLE, schema=CHUNKS_SCHEMA,
                                  exist_ok=True)


def files_table():
    """Open the ingest manifest table, creating it on first use."""
    return connect().create_table(FILES_TABLE, schema=FILES_SCHEMA,
                                  exist_ok=True)


def add_chunks(case_name: str, texts: list[str], metas: list[dict]) -> int:
    """Embed `texts` and append them to the chunks table.

    Every column is written explicitly: LanceDB silently null-fills any
    column a row dict omits, which would quietly corrupt the manifest.
    """
    if not texts:
        return 0
    if len(texts) != len(metas):
        raise StoreError(f"texts/metas length mismatch: "
                         f"{len(texts)} vs {len(metas)}")

    vectors = get_embeddings().embed_documents(texts)

    rows = []
    for text, meta, vec in zip(texts, metas, vectors):
        if len(vec) != EMBED_DIM:
            raise StoreError(f"embedding dim {len(vec)}, expected {EMBED_DIM}")
        raw_case = meta.get("case", case_name)
        rows.append({
            "id": str(uuid.uuid4()),
            "source_path": meta.get("source", ""),
            "filename": meta.get("filename", ""),
            "case_name": raw_case,
            "case_key": normalize_case(raw_case),
            "doc_type": meta.get("doc_type", ""),
            "page": int(meta.get("page", 0) or 0),
            "text": text,
            "vector": vec,
        })

    chunks_table().add(rows)
    return len(rows)


def mark_file_ingested(case_name: str, source_path: str,
                       chunk_count: int) -> None:
    """Record that a file is fully indexed.

    Called ONLY after that file's chunks are durably written. Completion is
    recorded, never inferred from chunk presence — a file interrupted
    mid-write must be redone on resume, not skipped forever.
    """
    files_table().add([{
        "source_path": source_path,
        "case_name": case_name,
        "case_key": normalize_case(case_name),
        "chunk_count": int(chunk_count),
        "completed_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }])


def _escape(value: str) -> str:
    """Escape a string literal for a LanceDB SQL filter."""
    return value.replace("'", "''")


def query_collection(case_name: str, query_text: str, k: int = 8,
                     doc_type_filter: str | None = None) -> list[dict]:
    """Semantic search within one case.

    Returns the same dict shape the ChromaDB implementation returned, so
    callers are unchanged.

    Metric note: queries pin cosine explicitly. ChromaDB returned SQUARED
    L2 and core._distance_to_relevance applied exp(-d) to it. LanceDB's
    default L2 is not squared, so reusing exp(-d) would silently rescale
    the relevance gate. With cosine, relevance is 1 - distance, which is
    bounded and scale-stable.

    Failure semantics mirror get_indexed_sources, and for the same reason:
    an empty result must mean "nothing matched", never "the database is
    broken". In a legal-evidence tool, silently answering "no documents
    found" because the index is unreadable is worse than raising — the user
    would conclude the material does not exist.
    """
    try:
        db = connect()
        names = db.list_tables().tables
    except Exception as e:
        raise StoreError(f"cannot open vector store: {e}") from e

    if CHUNKS_TABLE not in names:
        logger.info("No chunks table yet — nothing has been ingested.")
        return []

    where = f"case_key = '{_escape(normalize_case(case_name))}'"
    if doc_type_filter and doc_type_filter != "All":
        where += f" AND doc_type = '{_escape(doc_type_filter)}'"

    try:
        tbl = db.open_table(CHUNKS_TABLE)
        vec = get_embeddings().embed_query(query_text)
        raw = (tbl.search(vec)
                  .metric("cosine")
                  .where(where)
                  .limit(k)
                  .to_list())
    except Exception as e:
        raise StoreError(f"query failed for case '{case_name}': {e}") from e

    out = []
    for r in raw:
        distance = float(r.get("_distance", 0.0))
        out.append({
            "content": r.get("text") or "",
            "filename": r.get("filename") or os.path.basename(
                r.get("source_path") or "") or "unknown",
            "source": r.get("source_path") or "",
            "page": r.get("page", "?"),
            "doc_type": r.get("doc_type") or "",
            "case": r.get("case_name") or case_name,
            "score": max(0.0, 1.0 - distance),
            "distance": distance,
        })

    logger.info("query_collection('%s', '%s'): %d results, scores=[%s]",
                case_name, query_text[:50], len(out),
                ", ".join(f"{o['score']:.3f}" for o in out[:3]))
    return out


def get_indexed_sources(case_name: str) -> set[str]:
    """Source paths fully indexed for a case.

    Three distinct outcomes, deliberately:
      * manifest table absent  -> set()        (genuinely a new case)
      * query succeeds         -> the real set
      * anything else          -> raise StoreError

    Never collapse the third case into an empty set. Doing so makes a broken
    backend look like a brand-new case, and the caller then re-ingests the
    entire corpus. That is not hypothetical — it happened on 2026-07-21.

    Reads the manifest rather than inferring completion from chunk presence,
    so a file interrupted mid-write is redone instead of skipped forever.
    """
    try:
        db = connect()
    except Exception as e:
        raise StoreError(f"cannot open vector store: {e}") from e

    try:
        names = db.list_tables().tables
    except Exception as e:
        raise StoreError(f"cannot list tables: {e}") from e

    if FILES_TABLE not in names:
        logger.info("No ingest manifest yet — treating '%s' as a new case.",
                    case_name)
        return set()

    try:
        tbl = db.open_table(FILES_TABLE)
        arr = (tbl.search()
                  .where(f"case_key = '{_escape(normalize_case(case_name))}'")
                  .select(["source_path"])
                  .limit(None)
                  .to_arrow())
    except Exception as e:
        raise StoreError(f"cannot read ingest manifest: {e}") from e

    return set(arr.column("source_path").to_pylist()) - {""}


def list_cases() -> list[dict]:
    """All cases present in the store, with their chunk counts.

    Groups by case_key so name variants collapse, but reports the raw
    case_name so the UI shows what the user actually typed.

    Raises StoreError on backend failure — an empty list must mean "nothing
    ingested", not "could not read the index".
    """
    try:
        db = connect()
        names = db.list_tables().tables
    except Exception as e:
        raise StoreError(f"cannot open vector store: {e}") from e

    if CHUNKS_TABLE not in names:
        return []

    try:
        arr = (db.open_table(CHUNKS_TABLE)
                 .search()
                 .select(["case_name", "case_key"])
                 .limit(None)
                 .to_arrow())
    except Exception as e:
        raise StoreError(f"cannot read cases: {e}") from e

    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for raw, key in zip(arr.column("case_name").to_pylist(),
                        arr.column("case_key").to_pylist()):
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, raw)
    return [{"name": display[k], "chunks": counts[k]}
            for k in sorted(counts, key=lambda k: display[k])]


# ── Case management ──────────────────────────────────────────────────────────
# These operate on both tables. Because case is a column rather than a table
# name, rename is an UPDATE and delete is a DELETE — no data is copied.

def case_exists(case_name: str) -> bool:
    """True if any chunk is stored under this case."""
    return any(normalize_case(c["name"]) == normalize_case(case_name)
               for c in list_cases())


def delete_case(case_name: str) -> int:
    """Remove every chunk and manifest row for a case.

    Returns the number of chunks removed. Deleting the manifest rows too is
    essential: leaving them behind would make the next ingest believe those
    files are still indexed and skip them.
    """
    key = _escape(normalize_case(case_name))
    db = connect()
    names = db.list_tables().tables

    removed = 0
    if CHUNKS_TABLE in names:
        tbl = db.open_table(CHUNKS_TABLE)
        removed = tbl.count_rows(f"case_key = '{key}'")
        tbl.delete(f"case_key = '{key}'")
    if FILES_TABLE in names:
        db.open_table(FILES_TABLE).delete(f"case_key = '{key}'")

    drop_unread_pages(case_name)
    logger.info("Deleted case '%s' (%d chunks).", case_name, removed)
    return removed


def rename_case(old_name: str, new_name: str) -> int:
    """Rename a case in place. Returns the number of chunks affected."""
    old_key = _escape(normalize_case(old_name))
    new_key = normalize_case(new_name)
    if not new_key:
        raise StoreError("new case name is empty")
    if case_exists(new_name) and normalize_case(old_name) != new_key:
        raise StoreError(f"case '{new_name}' already exists")

    db = connect()
    names = db.list_tables().tables
    values = {"case_name": new_name, "case_key": new_key}

    affected = 0
    if CHUNKS_TABLE in names:
        res = db.open_table(CHUNKS_TABLE).update(
            where=f"case_key = '{old_key}'", values=values)
        affected = getattr(res, "rows_updated", 0)
    if FILES_TABLE in names:
        db.open_table(FILES_TABLE).update(
            where=f"case_key = '{old_key}'", values=values)

    rename_unread_pages(old_name, new_name)
    logger.info("Renamed case '%s' -> '%s' (%d chunks).",
                old_name, new_name, affected)
    return affected


# ── Pages that could not be read ────────────────────────────────────────────
# A page with no readable text (a scan without OCR, or a broken text layer
# OCR could not fix) is not indexed. It is recorded here instead, per case and
# file, so the app can say how much of the record is NOT searchable, and can
# re-read those files once OCR works. Kept as a small JSON file inside the
# database folder, so it moves with the database.
UNREAD_PAGES_FILE = "unread_pages.json"


def _unread_path() -> str:
    return os.path.join(VECTORS_DIR, UNREAD_PAGES_FILE)


def _load_unread() -> dict:
    try:
        with open(_unread_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s: %s", _unread_path(), e)
        return {}


def _save_unread(data: dict) -> None:
    os.makedirs(VECTORS_DIR, exist_ok=True)
    tmp = _unread_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, _unread_path())


def unread_pages(case_name: str) -> dict:
    """{source_path: [page, ...]} of unreadable pages recorded for a case."""
    return {k: list(v) for k, v in
            _load_unread().get(normalize_case(case_name), {}).items()}


def record_unread_pages(case_name: str, source_path: str, pages) -> None:
    """Set the unreadable pages of one file (an empty list clears it)."""
    data = _load_unread()
    key = normalize_case(case_name)
    files = data.get(key, {})
    pages = sorted({int(p) for p in pages or []})
    if pages:
        files[source_path] = pages
    elif source_path in files:
        files.pop(source_path)
    else:
        return                      # nothing recorded and nothing to clear
    if files:
        data[key] = files
    else:
        data.pop(key, None)
    _save_unread(data)


def drop_unread_pages(case_name: str) -> None:
    data = _load_unread()
    if data.pop(normalize_case(case_name), None) is not None:
        _save_unread(data)


def rename_unread_pages(old_name: str, new_name: str) -> None:
    data = _load_unread()
    old, new = normalize_case(old_name), normalize_case(new_name)
    if old in data and old != new:
        data[new] = {**data.get(new, {}), **data.pop(old)}
        _save_unread(data)


def clone_case(src_name: str, new_name: str) -> int:
    """Copy every chunk of a case under a new name. Returns chunks copied.

    Vectors are copied as-is rather than re-embedded, so this is cheap and
    cannot drift from the original.
    """
    new_key = normalize_case(new_name)
    if not new_key:
        raise StoreError("new case name is empty")
    if case_exists(new_name):
        raise StoreError(f"case '{new_name}' already exists")

    db = connect()
    names = db.list_tables().tables
    if CHUNKS_TABLE not in names:
        raise StoreError("nothing has been ingested yet")

    src_key = _escape(normalize_case(src_name))
    tbl = db.open_table(CHUNKS_TABLE)
    rows = (tbl.search()
               .where(f"case_key = '{src_key}'")
               .limit(None)
               .to_arrow()
               .to_pylist())
    if not rows:
        raise StoreError(f"case '{src_name}' is empty")

    for r in rows:
        r["id"] = str(uuid.uuid4())
        r["case_name"] = new_name
        r["case_key"] = new_key
        r.pop("_distance", None)
    tbl.add(rows)

    # Carry the manifest across too, so a re-ingest of the clone skips the
    # files that are already in it.
    if FILES_TABLE in names:
        ftbl = db.open_table(FILES_TABLE)
        frows = (ftbl.search()
                     .where(f"case_key = '{src_key}'")
                     .limit(None)
                     .to_arrow()
                     .to_pylist())
        for r in frows:
            r["case_name"] = new_name
            r["case_key"] = new_key
            r.pop("_distance", None)
        if frows:
            ftbl.add(frows)

    logger.info("Cloned case '%s' -> '%s' (%d chunks).",
                src_name, new_name, len(rows))
    return len(rows)


def forget_file(case_name: str, source_path: str) -> int:
    """Remove one file's chunks and its manifest row so it is re-ingested.

    The manifest row MUST go too. Deleting only the chunks would leave the
    file marked complete, and the next ingest would skip it forever --
    turning a fixable problem into permanent data loss.

    Returns the number of chunks removed.
    """
    key = _escape(normalize_case(case_name))
    src = _escape(source_path)
    db = connect()
    names = db.list_tables().tables

    removed = 0
    if CHUNKS_TABLE in names:
        tbl = db.open_table(CHUNKS_TABLE)
        cond = f"case_key = '{key}' AND source_path = '{src}'"
        removed = tbl.count_rows(cond)
        tbl.delete(cond)
    if FILES_TABLE in names:
        db.open_table(FILES_TABLE).delete(
            f"case_key = '{key}' AND source_path = '{src}'")

    logger.info("Forgot '%s' from case '%s' (%d chunks); it will be "
                "re-ingested on the next run.", source_path, case_name, removed)
    return removed


def copy_subset(src_case: str, dst_case: str, path_substring: str) -> int:
    """Copy chunks whose source_path contains `path_substring` into dst_case.

    Vectors are copied verbatim (no re-embed), and the matching manifest rows
    are copied too, so dst_case is complete and a re-ingest of it skips those
    files. Used to carve a docket-only case out of the full case.

    Returns the number of chunks copied.
    """
    dst_key = normalize_case(dst_case)
    if not dst_key:
        raise StoreError("destination case name is empty")
    if case_exists(dst_case):
        raise StoreError(f"case '{dst_case}' already exists")

    db = connect()
    names = db.list_tables().tables
    if CHUNKS_TABLE not in names:
        raise StoreError("nothing has been ingested yet")

    src_key = _escape(normalize_case(src_case))
    like = _escape(path_substring)
    tbl = db.open_table(CHUNKS_TABLE)
    rows = (tbl.search()
               .where(f"case_key = '{src_key}' AND source_path LIKE '%{like}%'")
               .limit(None).to_arrow().to_pylist())
    if not rows:
        raise StoreError(f"no chunks in '{src_case}' match '{path_substring}'")

    for r in rows:
        r["id"] = str(uuid.uuid4())
        r["case_name"] = dst_case
        r["case_key"] = dst_key
        r.pop("_distance", None)
    tbl.add(rows)

    if FILES_TABLE in names:
        ftbl = db.open_table(FILES_TABLE)
        frows = (ftbl.search()
                     .where(f"case_key = '{src_key}' AND source_path LIKE '%{like}%'")
                     .limit(None).to_arrow().to_pylist())
        for r in frows:
            r["case_name"] = dst_case
            r["case_key"] = dst_key
            r.pop("_distance", None)
        if frows:
            ftbl.add(frows)

    logger.info("Copied %d chunks matching '%s' from '%s' into new case '%s'.",
                len(rows), path_substring, src_case, dst_case)
    return len(rows)
