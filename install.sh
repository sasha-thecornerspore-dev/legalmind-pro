#!/usr/bin/env bash
# LegalMind Pro — macOS / Linux installer.
# Installs Python dependencies, and detects + offers to install the optional
# system tools (Tk for the GUI, Tesseract + Poppler for OCR) via your package
# manager. Pass -y to accept all prompts non-interactively.

DIR="$(cd "$(dirname "$0")" && pwd)"

ASSUME_YES=0
case "${1:-}" in
    -y|--yes) ASSUME_YES=1 ;;
esac

echo "========================================"
echo "  LegalMind Pro - installer"
echo "  Legal Document Intelligence (RAG)"
echo "========================================"
echo

have() { command -v "$1" >/dev/null 2>&1; }

confirm() {   # confirm "question"  -> 0 for yes
    if [ "$ASSUME_YES" = "1" ]; then
        return 0
    fi
    printf "  %s [Y/n] " "$1"
    read -r reply || reply=""
    case "$reply" in
        [nN]*) return 1 ;;
        *)     return 0 ;;
    esac
}

# ── Detect the package manager ───────────────────────────────────────────────
OS="$(uname -s)"
PM=""
PM_INSTALL=""
if [ "$OS" = "Darwin" ]; then
    if have brew; then
        PM="brew"; PM_INSTALL="brew install"
    fi
elif [ "$OS" = "Linux" ]; then
    if have apt-get; then
        PM="apt"; PM_INSTALL="sudo apt-get install -y"
    elif have dnf; then
        PM="dnf"; PM_INSTALL="sudo dnf install -y"
    elif have pacman; then
        PM="pacman"; PM_INSTALL="sudo pacman -S --noconfirm"
    fi
fi

pkg_name() {   # pkg_name <tk|tesseract|poppler> -> package name for $PM
    case "$1" in
        tk)
            case "$PM" in
                brew)   echo "python-tk" ;;
                apt)    echo "python3-tk" ;;
                dnf)    echo "python3-tkinter" ;;
                pacman) echo "tk" ;;
            esac ;;
        tesseract)
            case "$PM" in
                apt) echo "tesseract-ocr" ;;
                *)   echo "tesseract" ;;
            esac ;;
        poppler)
            case "$PM" in
                apt|dnf) echo "poppler-utils" ;;
                *)       echo "poppler" ;;
            esac ;;
    esac
}

# ── 1. Python 3 ──────────────────────────────────────────────────────────────
if ! have python3; then
    echo "[ERROR] python3 was not found."
    echo "        macOS:  brew install python   (then re-run this script)"
    echo "        or install from https://www.python.org/downloads/"
    exit 1
fi
echo "[1/4] Python found: $(python3 --version)"

# ── 2. Python dependencies (fail loudly if this step fails) ──────────────────
echo "[2/4] Installing Python packages (a few minutes on first run)..."
if ! python3 -m pip install -r "$DIR/requirements.txt" --quiet --disable-pip-version-check; then
    echo "[ERROR] pip failed to install dependencies. See the output above."
    exit 1
fi
echo "      [OK] Python packages installed."

# ── 3. Optional system tools: detect, then offer to install the missing ──────
echo "[3/4] Checking optional system tools..."
MISSING=""
if ! python3 -c "import tkinter" >/dev/null 2>&1; then MISSING="$MISSING tk"; fi
if ! have tesseract; then MISSING="$MISSING tesseract"; fi
if ! have pdftoppm;  then MISSING="$MISSING poppler"; fi
MISSING="$(echo "$MISSING" | sed 's/^ *//;s/ *$//')"

if [ -z "$MISSING" ]; then
    echo "      [OK] Tk, Tesseract and Poppler are all present."
else
    echo "      Missing: $MISSING"

    # No package manager on macOS -> offer to install Homebrew first.
    if [ -z "$PM" ] && [ "$OS" = "Darwin" ]; then
        echo
        echo "      Homebrew is the easiest way to add these on macOS."
        if confirm "Install Homebrew now?"; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            if have brew; then
                PM="brew"; PM_INSTALL="brew install"
            fi
        fi
    fi

    if [ -n "$PM" ]; then
        PKGS=""
        for tool in $MISSING; do
            PKGS="$PKGS $(pkg_name "$tool")"
        done
        PKGS="$(echo "$PKGS" | sed 's/^ *//;s/ *$//')"
        echo "      Will install via $PM:  $PKGS"
        if confirm "Install these now?"; then
            # shellcheck disable=SC2086
            if $PM_INSTALL $PKGS; then
                echo "      [OK] System tools installed."
            else
                echo "      [WARNING] Some packages failed - install them manually if needed."
            fi
        else
            echo "      Skipped. OCR needs Tesseract + Poppler; the GUI needs Tk to start."
        fi
    else
        echo "      No supported package manager found. Install manually:"
        echo "        macOS:  brew install python-tk tesseract poppler"
        echo "        Debian: sudo apt install python3-tk tesseract-ocr poppler-utils"
        echo "        Fedora: sudo dnf install python3-tkinter tesseract poppler-utils"
    fi
fi

# ── 4. AI provider notes ─────────────────────────────────────────────────────
echo "[4/4] AI analysis is optional - pick a provider in the app:"
echo "      Ollama (local):        https://ollama.com   then: ollama pull gemma"
echo "      Claude (subscription): install Claude Code (https://claude.com/claude-code),"
echo "                             run 'claude' once to log in - no API key needed"
echo
echo "========================================"
echo "  Done. Launch LegalMind Pro with:"
echo "    ./legalmind.command      (or double-click it in Finder)"
echo "    or:  python3 gui.py"
echo "========================================"
