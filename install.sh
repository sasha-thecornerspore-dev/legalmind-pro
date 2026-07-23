#!/usr/bin/env bash
# LegalMind Pro — macOS / Linux installer.
# Installs Python dependencies and prints setup notes for the optional pieces.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================"
echo "  LegalMind Pro — installer"
echo "  Legal Document Intelligence (RAG)"
echo "========================================"
echo

# 1. Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 was not found."
    echo "        macOS:  brew install python-tk   (Tk is required for the GUI)"
    echo "        or install from https://www.python.org/downloads/"
    exit 1
fi
echo "[1/3] Python found: $(python3 --version)"

# Warn early if Tk is missing (Homebrew python often ships without it).
if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "      [WARNING] Python has no 'tkinter' — the GUI will not start."
    echo "                macOS: brew install python-tk"
    echo "                Debian/Ubuntu: sudo apt install python3-tk"
fi

# 2. Dependencies
echo "[2/3] Installing Python packages (a few minutes on first run)..."
python3 -m pip install -r "$DIR/requirements.txt" --quiet --disable-pip-version-check
echo "      [OK] Python packages installed."

# 3. Optional components
echo "[3/3] Optional components:"
echo
echo "  OCR (scanned PDFs):"
echo "    macOS:         brew install tesseract poppler"
echo "    Debian/Ubuntu: sudo apt install tesseract-ocr poppler-utils"
echo
echo "  AI analysis (choose any in the app's Provider dropdown):"
echo "    Ollama (local):        https://ollama.com   then: ollama pull gemma"
echo "    Claude (subscription): install Claude Code (https://claude.com/claude-code),"
echo "                           run 'claude' once to log in — no API key needed"
echo
echo "========================================"
echo "  Done. Launch LegalMind Pro with:"
echo "    ./legalmind.command      (or double-click it in Finder)"
echo "    or:  python3 gui.py"
echo "========================================"
