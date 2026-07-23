"""macOS/Linux portability of platform-specific bits."""
import os
import subprocess
import sys

import core


def test_open_path_uses_open_on_macos(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", lambda argv, *a, **k: calls.append(argv))
    core.open_path("/tmp/x.pdf")
    assert calls == [["open", "/tmp/x.pdf"]]


def test_open_path_uses_xdg_on_linux(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", lambda argv, *a, **k: calls.append(argv))
    core.open_path("/tmp/x")
    assert calls == [["xdg-open", "/tmp/x"]]


def test_open_path_never_raises(monkeypatch):
    """A failing opener must not crash the GUI."""
    monkeypatch.setattr(sys, "platform", "linux")
    def boom(*a, **k):
        raise FileNotFoundError("no xdg-open")
    monkeypatch.setattr(subprocess, "Popen", boom)
    core.open_path("/tmp/x")   # must not raise


def test_tesseract_default_is_windows_path_on_win(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert core._default_tesseract().endswith("tesseract.exe")


def test_tesseract_default_is_bare_or_unix_on_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    # No Homebrew paths on the CI box -> falls back to the bare command on PATH.
    d = core._default_tesseract()
    assert d == "tesseract" or d.endswith("/tesseract")
    assert not d.endswith(".exe")


def test_poppler_default_empty_or_dir_on_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    d = core._default_poppler()
    assert d == "" or os.path.isdir(d)
