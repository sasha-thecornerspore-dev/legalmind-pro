"""LegalMind diagnostics — inspect the LanceDB vector store.

Run:  python diagnose.py

Reports what is actually in the index: cases, chunk counts, how many files
each case has completed, and a sample query per case so retrieval can be
eyeballed without launching the GUI.

Also flags TORN FILES — files that have chunks but no manifest row, meaning
a run died partway through writing them. Those are re-ingested next run.
"""
import os
import sys
import traceback


def _fmt(n) -> str:
    return f"{n:,}"


def main():
    print("=" * 62)
    print("  LegalMind Pro — vector store diagnostics")
    print("=" * 62)

    try:
        import store
        from core import EMBED_MODEL, EMBED_DIM
    except Exception as e:
        print(f"\n  FAILED to import the store module: {e}\n")
        traceback.print_exc()
        return 1

    print(f"\n  Store path:  {store.VECTORS_DIR}")
    print(f"  Exists:      {os.path.exists(store.VECTORS_DIR)}")
    print(f"  Model:       {EMBED_MODEL}")
    print(f"  Dimension:   {EMBED_DIM}")

    if not os.path.isdir(EMBED_MODEL):
        print(f"\n  WARNING: model directory is missing: {EMBED_MODEL}")
        print("  Rebuild it with:  python tools/rebuild_model.py")

    if not os.path.exists(store.VECTORS_DIR):
        print("\n  Nothing has been ingested yet — run an ingest first.\n")
        return 0

    try:
        db = store.connect()
        tables = db.list_tables().tables
    except store.StoreError as e:
        print(f"\n  STORE ERROR: {e}\n")
        return 1
    except Exception as e:
        print(f"\n  FAILED to open the store: {e}\n")
        traceback.print_exc()
        return 1

    print(f"  Tables:      {tables or '(none)'}")

    if store.CHUNKS_TABLE not in tables:
        print("\n  No chunks table — nothing has been ingested yet.\n")
        return 0

    try:
        cases = store.list_cases()
    except store.StoreError as e:
        print(f"\n  STORE ERROR reading cases: {e}\n")
        return 1

    total = store.chunks_table().count_rows()
    print(f"\n  Total chunks: {_fmt(total)}")
    print(f"  Cases:        {len(cases)}")

    if not cases:
        print("\n  The table exists but holds no rows.\n")
        return 0

    print("\n" + "-" * 62)
    for c in cases:
        name = c["name"]
        print(f"\n  CASE: {name}")
        print(f"    chunks: {_fmt(c['chunks'])}")

        try:
            done = store.get_indexed_sources(name)
            print(f"    files completed (manifest): {_fmt(len(done))}")
        except store.StoreError as e:
            # Worth shouting about: silent failure of this seam caused a
            # full duplicate re-ingest on 2026-07-21.
            print(f"    MANIFEST UNREADABLE: {e}")
            continue

        try:
            key = store.normalize_case(name)
            arr = (store.chunks_table().search()
                   .where(f"case_key = '{key}'")
                   .select(["source_path"])
                   .limit(None)
                   .to_arrow())
            seen = set(arr.column("source_path").to_pylist()) - {""}
            torn = seen - done
            if torn:
                print(f"    WARNING: {len(torn)} file(s) have chunks but no "
                      f"manifest row (interrupted mid-write):")
                for p in sorted(torn)[:5]:
                    print(f"      - {p}")
                if len(torn) > 5:
                    print(f"      ... and {len(torn) - 5} more")
                print("    These will be re-ingested on the next run.")
        except Exception as e:
            print(f"    (could not check for torn files: {e})")

        try:
            hits = store.query_collection(name, "motion order affidavit", k=3)
            if hits:
                print("    sample query 'motion order affidavit':")
                for h in hits:
                    print(f"      {h['score']:.3f}  {h['filename']} "
                          f"p.{h['page']}")
            else:
                print("    sample query returned nothing.")
        except store.StoreError as e:
            print(f"    QUERY FAILED: {e}")

    print("\n" + "=" * 62)
    print("  Done.")
    print("=" * 62 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
