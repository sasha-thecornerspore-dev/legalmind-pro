#!/usr/bin/env bash
# LegalMind Pro launcher for macOS (double-click in Finder) and Linux.
# Runs the GUI from this script's own directory.
cd "$(dirname "$0")"
exec python3 gui.py
