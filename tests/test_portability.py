"""Guards that keep the app usable on a clean clone by anyone.

These check the *shape* of portability problems (absolute user-home paths,
a nonexistent hardcoded model path) rather than naming any individual, so the
test file itself carries no personal data.
"""
import glob
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Absolute, machine-specific home paths that would break on someone else's box:
# a Windows drive + Users dir, or a POSIX /Users or /home directory, each
# followed by an account name. (Patterns assembled from parts so this file does
# not itself contain a literal home path that would match.)
_HOME_PATH = re.compile(
    r"[A-Za-z]:" + re.escape("\\") + "Users" + re.escape("\\") + r"[^\\/\"'\s]+"
    r"|/" + "Users" + r"/[^/\"'\s]+"
    r"|/" + "home" + r"/[^/\"'\s]+")


def _shipped_py_files():
    """Python files that would actually be published (git-tracked only).

    Gitignored, machine-specific run scripts are excluded because they never
    ship. Falls back to a filesystem glob if git is unavailable.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=15, check=True).stdout
        files = [os.path.join(REPO_ROOT, p) for p in out.splitlines() if p.strip()]
        if files:
            return files
    except Exception:
        pass
    return glob.glob(os.path.join(REPO_ROOT, "*.py")) + \
        glob.glob(os.path.join(REPO_ROOT, "tools", "*.py"))


def test_embed_model_falls_back_to_hub_when_local_absent():
    """A fresh clone has no models/ dir; the model id must then be a
    downloadable hub id, not a nonexistent local path (which would crash)."""
    import core
    if os.path.isdir(core.EMBED_MODEL_DIR):
        assert core.EMBED_MODEL == core.EMBED_MODEL_DIR
    else:
        assert core.EMBED_MODEL == core.EMBED_MODEL_HUB
    assert core.EMBED_MODEL_HUB == "sentence-transformers/all-MiniLM-L6-v2"


def test_no_absolute_home_paths_in_shipped_source():
    """No absolute user-home path baked into shipped Python."""
    offenders = []
    for f in _shipped_py_files():
        text = open(f, encoding="utf-8", errors="replace").read()
        if _HOME_PATH.search(text):
            offenders.append(os.path.basename(f))
    assert not offenders, f"absolute home paths in: {offenders}"


def test_github_repo_is_overridable(monkeypatch):
    """Forks can repoint the updater without editing source."""
    import importlib
    import core
    monkeypatch.setenv("LEGALMIND_REPO", "someone/their-fork")
    importlib.reload(core)
    try:
        assert core.GITHUB_REPO == "someone/their-fork"
    finally:
        monkeypatch.delenv("LEGALMIND_REPO")
        importlib.reload(core)
