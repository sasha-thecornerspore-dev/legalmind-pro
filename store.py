"""LanceDB vector store for LegalMind Pro.

This is the ONLY module that talks to the vector database. Everything else
goes through the functions here.

IMPORTANT: the data directory is `vectors/`, never `lancedb/`. A directory
named `lancedb` in the repo root shadows the real library as an empty
namespace package, because the app runs with the repo as its cwd.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import uuid

import pyarrow as pa

from core import BASE_DIR, EMBED_DIM, get_embeddings

logger = logging.getLogger("legalmind.store")

VECTORS_DIR = os.path.join(BASE_DIR, "vectors")

CHUNKS_TABLE = "chunks"
FILES_TABLE = "ingested_files"

_db = None


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

    logger.info("Renamed case '%s' -> '%s' (%d chunks).",
                old_name, new_name, affected)
    return affected


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
