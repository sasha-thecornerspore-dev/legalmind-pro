"""Rebuild a usable all-MiniLM-L6-v2 directory from a broken HF cache.

The HuggingFace cache on A: has intact blobs but 0-byte snapshot files: HF
stores snapshot entries as symlinks into blobs/, and Google Drive (which this
cache was synced through) cannot sync symlinks, so they arrived as empty files.

Blobs are named by SHA, not by filename, so each one is identified by its
CONTENT and written out under its proper name.

Usage:  C:/Python314/python.exe tools/rebuild_model.py
"""
import json
import os
import shutil
import sys

# Path to the HuggingFace cache `blobs/` directory for all-MiniLM-L6-v2.
# Override with argv[1] or the MINILM_BLOBS env var; otherwise the standard
# per-user HF cache location is used.
_DEFAULT_BLOBS = os.path.join(
    os.path.expanduser("~"), ".cache", "huggingface", "hub",
    "models--sentence-transformers--all-MiniLM-L6-v2", "blobs")
BLOBS = (sys.argv[1] if len(sys.argv) > 1
         else os.environ.get("MINILM_BLOBS", _DEFAULT_BLOBS))

DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "models", "all-MiniLM-L6-v2")

# Everything sentence-transformers needs to load the model.
REQUIRED = {
    "model.safetensors",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "special_tokens_map.json",
    "modules.json",
    "sentence_bert_config.json",
    "config_sentence_transformers.json",
    os.path.join("1_Pooling", "config.json"),
}


def identify(path: str) -> str | None:
    """Return the destination filename for a blob, or None to skip it.

    Order matters: tokenizer_config.json and special_tokens_map.json share
    keys, so the more specific test has to come first.
    """
    size = os.path.getsize(path)
    raw = open(path, "rb").read()
    head = raw[:1]

    if head not in (b"{", b"[", b"-"):
        # Binary payload => the weights.
        return "model.safetensors" if size > 1_000_000 else None

    if head == b"-":
        return None  # YAML frontmatter => README.md, not needed to load

    try:
        obj = json.loads(raw)
    except Exception:
        # vocab.txt starts with "[PAD]" so it looks JSON-ish but will not parse.
        return "vocab.txt" if size > 100_000 else None

    if isinstance(obj, list):
        return "modules.json"

    keys = set(obj)
    if "architectures" in keys:
        return "config.json"
    if "max_seq_length" in keys:
        return "sentence_bert_config.json"
    if "word_embedding_dimension" in keys:
        return os.path.join("1_Pooling", "config.json")
    if "__version__" in keys:
        return "config_sentence_transformers.json"
    if "truncation" in keys or "added_tokens" in keys:
        return "tokenizer.json"
    if "do_lower_case" in keys:
        return "tokenizer_config.json"
    if "unk_token" in keys:
        return "special_tokens_map.json"
    return None


def main() -> None:
    if not os.path.isdir(BLOBS):
        raise SystemExit(f"blobs directory not found: {BLOBS}\n"
                         "Is the A: volume mounted?")

    os.makedirs(os.path.join(DEST, "1_Pooling"), exist_ok=True)

    written: dict[str, int] = {}
    for name in sorted(os.listdir(BLOBS)):
        src = os.path.join(BLOBS, name)
        if not os.path.isfile(src):
            continue
        dest_name = identify(src)
        if dest_name is None:
            continue
        if dest_name in written:
            print(f"  ! duplicate mapping for {dest_name} "
                  f"(blob {name[:12]}), keeping the first")
            continue
        dest = os.path.join(DEST, dest_name)
        shutil.copyfile(src, dest)
        written[dest_name] = os.path.getsize(dest)

    for n in sorted(written):
        print(f"{written[n]:>10}  {n}")

    missing = REQUIRED - set(written)
    empty = {n for n, s in written.items() if s == 0}

    print(f"\n{len(written)} file(s) written to {DEST}")
    if missing:
        raise SystemExit(f"FAILED - missing required file(s): {sorted(missing)}")
    if empty:
        raise SystemExit(f"FAILED - zero-byte output(s): {sorted(empty)}")
    print("All required files present and non-empty.")


if __name__ == "__main__":
    main()
