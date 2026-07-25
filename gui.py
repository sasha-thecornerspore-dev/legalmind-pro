"""
LegalMind Pro v10.0 -- Full GUI with chat interface, document preview,
modern dark-glass aesthetic, and pause/stop ingest.

Features:
  - Chat tab:    conversational interface that auto-detects RAG vs general chat,
                 document upload for ad-hoc analysis, and a collapsible preview pane
  - Ingest tab:  pause, resume, and stop ingestion without losing progress
  - Cases tab:   manage indexed case collections
  - Settings:    LLM providers, OCR paths, system info
"""

import io
import logging
import os
import sys
import threading
import time
import json

# -- pythonw stdout/stderr fix ------------------------------------------------
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# -- Global subprocess console-window suppression (Windows) -------------------
# Must run BEFORE any library that spawns subprocesses (pytesseract, pdf2image,
# huggingface, chromadb, etc.) is imported, otherwise those calls flash a CMD
# window on every invocation -- making typing impossible during ingest/OCR.
def _silence_subprocess_windows():
    if sys.platform != "win32":
        return
    try:
        import subprocess as _sp
        _OrigPopen = _sp.Popen

        class _SilentPopen(_OrigPopen):
            def __init__(self, *a, **kw):
                if not kw.get("startupinfo"):
                    si = _sp.STARTUPINFO()
                    si.dwFlags |= _sp.STARTF_USESHOWWINDOW
                    si.wShowWindow = 0  # SW_HIDE
                    kw["startupinfo"] = si
                if "creationflags" not in kw:
                    kw["creationflags"] = _sp.CREATE_NO_WINDOW
                super().__init__(*a, **kw)

        _sp.Popen = _SilentPopen
    except Exception:
        pass

_silence_subprocess_windows()

import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox, scrolledtext
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store
from store import list_cases, query_collection, VECTORS_DIR
from core import (
    classify_doc_type, VERSION, open_path,
    query_ollama, list_ollama_models, DEFAULT_OLLAMA_MODEL,
    get_tesseract_cmd, get_poppler_path, save_settings, _load_settings,
    query_llm, query_claude, query_openai, LLM_PROVIDERS,
    ANALYSIS_MODES, get_analysis_prompt,
    DEFAULT_CLAUDE_MODEL, DEFAULT_OPENAI_MODEL, DEFAULT_OPENAI_URL,
    query_llm_stream, query_claude_messages, query_openai_messages,
)


def _app_version() -> str:
    """Current app version string, from the single source of truth in core."""
    return VERSION

# Minimum relevance (1 - cosine distance) for a retrieved chunk to be shown
# to the model. Calibrated 2026-07-22 against the 08_Pleadings pilot: 12
# on-topic probes scored 0.384-0.719, 8 deliberately unrelated probes scored
# 0.117-0.304. The previous value of 0.10 admitted all 8 noise probes.
# Re-validate with tools/pilot.py after a large ingest.
RELEVANCE_GATE = 0.35

# Persistent chat sessions live next to the project root
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "chat_sessions")
from ingest_engine import (
    IngestProgress, IngestResult, ingest_folder, discover_pdfs,
    get_existing_sources, check_ocr, get_ocr_status, LOG_FILE,
)

logger = logging.getLogger("legalmind.gui")

# =============================================================================
# Dark Glass Theme
# =============================================================================
# Deep charcoal base with soft luminous accents
BG           = "#0d0d1a"       # deepest background
BG_MID       = "#151528"       # card/panel background
BG_CARD      = "#1c1c35"       # elevated cards
BG_ENTRY     = "#22223a"       # input fields
BG_HOVER     = "#2a2a4a"       # hover state
BORDER       = "#2e2e50"       # subtle borders
BORDER_GLOW  = "#4a4a7a"       # active/focus border glow

FG           = "#e0e0f0"       # primary text
FG_DIM       = "#8888aa"       # secondary text
FG_MUTED     = "#5e5e7e"       # muted/placeholder

ACCENT       = "#7c6bf0"       # primary accent (soft purple)
ACCENT_LIGHT = "#a78bfa"       # lighter accent
ACCENT_HOVER = "#6d5ce0"       # accent hover
ACCENT_DIM   = "#4a3d8f"       # dimmed accent

TEAL         = "#2dd4bf"       # success / positive
ROSE         = "#f472b6"       # error / warning
AMBER        = "#fbbf24"       # caution
SKY          = "#38bdf8"       # info / links
EMERALD      = "#34d399"       # confirmation

# Chat bubble colors
CHAT_USER_BG = "#252548"       # user message bubble
CHAT_AI_BG   = "#1a1a35"       # AI response bubble
CHAT_RAG_BG  = "#1a2535"       # RAG context indicator

FONT         = ("Segoe UI", 10)
FONT_BOLD    = ("Segoe UI", 10, "bold")
FONT_HEADING = ("Segoe UI", 13, "bold")
FONT_MONO    = ("Cascadia Code", 9)
FONT_TITLE   = ("Segoe UI", 20, "bold")
FONT_CHAT    = ("Segoe UI", 10)
FONT_SMALL   = ("Segoe UI", 8)


def apply_dark_glass_theme(root):
    """Apply the modern dark-glass theme to ttk widgets."""
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=FG, font=FONT,
                    borderwidth=0, focuscolor=ACCENT)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("TButton", background=BG_CARD, foreground=FG, font=FONT_BOLD,
                    padding=(14, 7))
    style.map("TButton",
              background=[("active", BG_HOVER), ("pressed", ACCENT_DIM)],
              foreground=[("active", FG), ("pressed", FG)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    font=FONT_BOLD, padding=(16, 8))
    style.map("Accent.TButton",
              background=[("active", ACCENT_HOVER), ("pressed", ACCENT_DIM)])

    style.configure("Teal.TButton", background=TEAL, foreground=BG,
                    font=FONT_BOLD, padding=(14, 7))
    style.map("Teal.TButton",
              background=[("active", "#5eead4")])

    style.configure("Danger.TButton", background=ROSE, foreground=BG,
                    font=FONT_BOLD, padding=(14, 7))
    style.map("Danger.TButton",
              background=[("active", "#f9a8d4")])

    style.configure("Amber.TButton", background=AMBER, foreground=BG,
                    font=FONT_BOLD, padding=(14, 7))
    style.map("Amber.TButton",
              background=[("active", "#fcd34d")])

    style.configure("Ghost.TButton", background=BG, foreground=FG_DIM,
                    font=FONT, padding=(10, 6))
    style.map("Ghost.TButton",
              background=[("active", BG_CARD)],
              foreground=[("active", FG)])

    # Notebook tabs
    style.configure("TNotebook", background=BG, borderwidth=0, padding=0)
    style.configure("TNotebook.Tab", background=BG_MID, foreground=FG_DIM,
                    font=FONT_BOLD, padding=(20, 10))
    style.map("TNotebook.Tab",
              background=[("selected", BG)],
              foreground=[("selected", ACCENT_LIGHT)])

    # Entries
    style.configure("TEntry", fieldbackground=BG_ENTRY, foreground=FG, font=FONT,
                    insertcolor=FG, padding=6)
    style.configure("TCombobox", fieldbackground=BG_ENTRY, foreground=FG, font=FONT,
                    padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", BG_ENTRY)])

    # Progress bars
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG_MID,
                    thickness=14)
    style.configure("Teal.Horizontal.TProgressbar", background=TEAL, troughcolor=BG_MID,
                    thickness=14)

    # Label frames
    style.configure("TLabelframe", background=BG_MID, foreground=ACCENT_LIGHT,
                    font=FONT_BOLD)
    style.configure("TLabelframe.Label", background=BG_MID, foreground=ACCENT_LIGHT,
                    font=FONT_BOLD)

    # Custom label styles
    style.configure("Heading.TLabel", font=FONT_HEADING, foreground=ACCENT_LIGHT, background=BG)
    style.configure("Title.TLabel", font=FONT_TITLE, foreground=FG, background=BG)
    style.configure("Dim.TLabel", foreground=FG_DIM, background=BG, font=FONT)
    style.configure("Success.TLabel", foreground=TEAL, background=BG, font=FONT_BOLD)
    style.configure("Error.TLabel", foreground=ROSE, background=BG, font=FONT_BOLD)
    style.configure("Warn.TLabel", foreground=AMBER, background=BG, font=FONT)
    style.configure("Card.TFrame", background=BG_CARD)
    style.configure("CardLabel.TLabel", background=BG_CARD, foreground=FG)

    # Treeview
    style.configure("Treeview", background=BG_CARD, foreground=FG,
                    fieldbackground=BG_CARD, font=FONT, rowheight=32, borderwidth=0)
    style.configure("Treeview.Heading", background=BG_MID, foreground=ACCENT_LIGHT,
                    font=FONT_BOLD)
    style.map("Treeview",
              background=[("selected", ACCENT_DIM)],
              foreground=[("selected", FG)])

    # Checkbutton
    style.configure("TCheckbutton", background=BG, foreground=FG, font=FONT)


# =============================================================================
# Main Application
# =============================================================================
class LegalMindApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LegalMind Pro")
        self.root.geometry("1200x800")
        self.root.minsize(1000, 700)
        self.root.configure(bg=BG)
        apply_dark_glass_theme(self.root)

        # State
        self._cancel_ingest = False
        self._pause_ingest = False
        self._ingest_running = False
        self._chat_history = []       # list of {"role": "user"|"assistant", "content": str}
        self._attached_files = []     # list of {"name": str, "text": str, "path": str}
        self._preview_visible = False
        self._current_session_path = None  # path of the autosaved session, if any

        # Header bar
        header = tk.Frame(root, bg=BG_MID, height=56)
        header.pack(fill="x", padx=0, pady=0)
        header.pack_propagate(False)

        header_inner = tk.Frame(header, bg=BG_MID)
        header_inner.pack(fill="both", expand=True, padx=20)

        # Title with subtle glow effect
        tk.Label(header_inner, text="LegalMind", font=("Segoe UI", 18, "bold"),
                 bg=BG_MID, fg=ACCENT_LIGHT).pack(side="left", pady=8)
        tk.Label(header_inner, text="Pro", font=("Segoe UI", 18),
                 bg=BG_MID, fg=FG_DIM).pack(side="left", pady=8)
        tk.Label(header_inner, text="Legal Document Intelligence",
                 font=FONT, bg=BG_MID, fg=FG_MUTED).pack(side="left", padx=(16, 0), pady=8)

        # Header actions
        log_btn = tk.Button(header_inner, text="View Log", font=FONT,
                            bg=BG_CARD, fg=FG_DIM, bd=0, padx=12, pady=4,
                            activebackground=BG_HOVER, activeforeground=FG,
                            cursor="hand2", command=self._open_log_file)
        log_btn.pack(side="right", padx=4, pady=12)

        # Accent line under header
        tk.Frame(root, bg=ACCENT_DIM, height=1).pack(fill="x")

        # Notebook
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_chat_tab()
        self._build_ingest_tab()
        self._build_cases_tab()
        self._build_settings_tab()

        # Status bar
        status_frame = tk.Frame(root, bg=BG_MID, height=28)
        status_frame.pack(fill="x", side="bottom")
        status_frame.pack_propagate(False)
        self.status_label = tk.Label(status_frame, text="Ready", font=FONT_SMALL,
                                     bg=BG_MID, fg=FG_MUTED, anchor="w", padx=16)
        self.status_label.pack(side="left", fill="x", expand=True)
        from core import VERSION as _APP_VERSION
        self.status_version = tk.Label(status_frame,
                                       text=f"LegalMind Pro v{_APP_VERSION}",
                                       font=FONT_SMALL, bg=BG_MID, fg=FG_MUTED, padx=16)
        self.status_version.pack(side="right")

        # Load initial data
        self.root.after(500, self._refresh_case_lists)

        # Check for a newer release in the background (non-blocking, silent
        # when up to date). Delayed so it never competes with startup.
        self.root.after(3000, lambda: self._check_for_updates(silent=True))

        # Global keyboard shortcuts
        self._bind_shortcuts()

    # -- Updates ---------------------------------------------------------------
    def _check_for_updates(self, silent: bool = True):
        """Check GitHub for a newer release, in a worker thread.

        silent=True: only speak up if an update exists (startup check).
        silent=False: also report "up to date" (manual Settings button).
        """
        def run():
            try:
                import updater
                info = updater.check_for_update()
            except Exception as e:
                logger.info("update check failed: %s", e)
                info = None
                if not silent:
                    self.root.after(0, messagebox.showinfo, "Updates",
                                    "Could not check for updates right now.")
                return
            if info:
                self.root.after(0, self._show_update_prompt, info)
            elif not silent:
                from core import VERSION as _v
                self.root.after(0, messagebox.showinfo, "Updates",
                                f"LegalMind Pro v{_v} is up to date.")
        threading.Thread(target=run, daemon=True, name="update-check").start()

    def _show_update_prompt(self, info: dict):
        """Offer the update: open the release page (download the installer)."""
        from core import VERSION as _v
        notes = (info.get("notes") or "").strip()
        if len(notes) > 400:
            notes = notes[:400] + "…"
        msg = (f"A newer version is available.\n\n"
               f"    Installed:  v{_v}\n"
               f"    Latest:     v{info['version']}\n\n"
               f"{notes}\n\nOpen the download page now?")
        if messagebox.askyesno("Update available", msg):
            import webbrowser
            webbrowser.open(info.get("page") or
                            f"https://github.com/{__import__('core').GITHUB_REPO}/releases/latest")

    # -- Keyboard shortcuts ----------------------------------------------------
    def _bind_shortcuts(self):
        r = self.root
        # Tab switching: Ctrl+1..4
        for i in range(4):
            r.bind_all(f"<Control-Key-{i+1}>",
                       lambda e, idx=i: self._select_tab(idx))
        # Chat actions
        r.bind_all("<Control-l>", lambda e: self._clear_chat_safe())
        r.bind_all("<Control-L>", lambda e: self._clear_chat_safe())
        r.bind_all("<Control-e>", lambda e: self._export_chat_safe())
        r.bind_all("<Control-E>", lambda e: self._export_chat_safe())
        r.bind_all("<Control-k>", lambda e: self._focus_chat_input())
        r.bind_all("<Control-K>", lambda e: self._focus_chat_input())
        # F1 -> open log
        r.bind_all("<F1>", lambda e: self._open_log_file())

    def _select_tab(self, idx: int):
        try:
            tabs = self.notebook.tabs()
            if 0 <= idx < len(tabs):
                self.notebook.select(tabs[idx])
        except Exception:
            pass

    def _clear_chat_safe(self):
        # Only clear when Chat tab is active, to avoid hijacking Ctrl+L in entries
        try:
            if self.notebook.index(self.notebook.select()) == 0:
                self._clear_chat()
        except Exception:
            pass
        return "break"

    def _export_chat_safe(self):
        try:
            if self.notebook.index(self.notebook.select()) == 0:
                self._export_chat()
        except Exception:
            pass
        return "break"

    def _focus_chat_input(self):
        try:
            self.notebook.select(self.notebook.tabs()[0])
            self._chat_input.focus_set()
        except Exception:
            pass
        return "break"

    # -- Utility ---------------------------------------------------------------
    def _persist_pref(self, key: str, value):
        try:
            s = _load_settings()
            s[key] = value
            save_settings(s)
        except Exception as e:
            logger.debug("persist_pref(%s) failed: %s", key, e)

    def _update_status(self, text: str):
        self.status_label.configure(text=text)
        if hasattr(self, '_status_timer'):
            self.root.after_cancel(self._status_timer)
        self._status_timer = self.root.after(8000,
            lambda: self.status_label.configure(text="Ready"))

    def _open_log_file(self):
        log_path = LOG_FILE
        if not os.path.exists(log_path):
            messagebox.showinfo("No Log", f"Log file not found yet:\n{log_path}")
            return
        open_path(log_path)

    def _change_db_location(self):
        """Point the app at a different database folder (e.g. an existing one
        after a fresh install). Persists the choice and reloads the cases."""
        import store
        current = store.VECTORS_DIR
        chosen = filedialog.askdirectory(
            title="Select the LegalMind database folder",
            initialdir=current if os.path.isdir(current) else os.path.expanduser("~"))
        if not chosen:
            return
        try:
            store.set_vectors_dir(chosen)
            cases = store.list_cases()
        except Exception as e:
            messagebox.showerror(
                "Database", f"Could not open that folder as a database:\n{e}")
            return
        self.db_path_label.configure(text=store.VECTORS_DIR)
        self._refresh_case_lists()
        n = len(cases)
        total = sum(c.get("chunks", 0) for c in cases)
        if n:
            messagebox.showinfo(
                "Database changed",
                f"Now using:\n{store.VECTORS_DIR}\n\n"
                f"Found {n} case(s), {total:,} chunks.")
        else:
            hint = ("" if store.looks_like_store(chosen) else
                    "\n\n(No existing index here yet — it will be created on "
                    "first ingest.)")
            messagebox.showinfo(
                "Database changed", f"Now using:\n{store.VECTORS_DIR}{hint}")

    # =========================================================================
    # CHAT TAB (replaces old Query tab)
    # =========================================================================
    def _build_chat_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Chat  ")

        # Main horizontal split: chat area | preview pane
        self._chat_pane = tk.PanedWindow(tab, orient="horizontal", bg=BG,
                                          sashwidth=4, sashrelief="flat",
                                          bd=0)
        self._chat_pane.pack(fill="both", expand=True)

        # Left side: chat interface
        chat_frame = tk.Frame(self._chat_pane, bg=BG)
        self._chat_pane.add(chat_frame, stretch="always", minsize=500)

        # Top bar: case selector + options
        top_bar = tk.Frame(chat_frame, bg=BG_MID, height=44)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        top_inner = tk.Frame(top_bar, bg=BG_MID)
        top_inner.pack(fill="both", expand=True, padx=16)

        tk.Label(top_inner, text="Case:", font=FONT, bg=BG_MID, fg=FG_DIM
                 ).pack(side="left", pady=8)
        self.query_case_var = tk.StringVar()
        case_combo = ttk.Combobox(top_inner, textvariable=self.query_case_var,
                                   width=18, state="readonly")
        case_combo.pack(side="left", padx=(6, 16), pady=8)
        case_combo.bind("<<ComboboxSelected>>",
                        lambda e: self._persist_pref("last_case",
                                                     self.query_case_var.get()))

        tk.Label(top_inner, text="Provider:", font=FONT, bg=BG_MID, fg=FG_DIM
                 ).pack(side="left", pady=8)
        saved = _load_settings()
        self.llm_provider_var = tk.StringVar(value=saved.get("llm_provider", "ollama"))
        provider_combo = ttk.Combobox(top_inner, textvariable=self.llm_provider_var,
                                      values=LLM_PROVIDERS,
                                      width=11, state="readonly")
        provider_combo.pack(side="left", padx=(6, 16), pady=8)
        provider_combo.bind("<<ComboboxSelected>>",
                            lambda e: self._persist_pref("llm_provider",
                                                         self.llm_provider_var.get()))

        tk.Label(top_inner, text="Mode:", font=FONT, bg=BG_MID, fg=FG_DIM
                 ).pack(side="left", pady=8)
        mode_names = [v["name"] for v in ANALYSIS_MODES.values()]
        self.analysis_mode_var = tk.StringVar(value="Standard Analysis")
        ttk.Combobox(top_inner, textvariable=self.analysis_mode_var,
                     values=mode_names, width=20, state="readonly"
                     ).pack(side="left", padx=(6, 16), pady=8)

        # Sessions button (recent chats)
        self._sessions_btn = tk.Button(
            top_inner, text="Sessions ▾", font=FONT_SMALL,
            bg=BG_CARD, fg=FG_DIM, bd=0, padx=10, pady=2,
            activebackground=BG_HOVER, activeforeground=FG,
            cursor="hand2", command=self._show_sessions_menu)
        self._sessions_btn.pack(side="right", pady=8, padx=(0, 6))

        # Preview toggle button
        self._preview_btn = tk.Button(
            top_inner, text="Doc Preview >>", font=FONT_SMALL,
            bg=BG_CARD, fg=FG_DIM, bd=0, padx=10, pady=2,
            activebackground=BG_HOVER, activeforeground=FG,
            cursor="hand2", command=self._toggle_preview)
        self._preview_btn.pack(side="right", pady=8)

        tk.Frame(chat_frame, bg=BORDER, height=1).pack(fill="x")

        # Chat messages area
        self._chat_canvas = tk.Canvas(chat_frame, bg=BG, highlightthickness=0,
                                       bd=0)
        chat_scroll = ttk.Scrollbar(chat_frame, orient="vertical",
                                     command=self._chat_canvas.yview)
        self._chat_messages_frame = tk.Frame(self._chat_canvas, bg=BG)

        self._chat_messages_frame.bind(
            "<Configure>",
            lambda e: self._chat_canvas.configure(
                scrollregion=self._chat_canvas.bbox("all")))

        self._chat_canvas_window = self._chat_canvas.create_window(
            (0, 0), window=self._chat_messages_frame, anchor="nw")

        self._chat_canvas.configure(yscrollcommand=chat_scroll.set)

        chat_scroll.pack(side="right", fill="y")
        self._chat_canvas.pack(side="top", fill="both", expand=True)

        # Make chat messages frame resize with canvas
        self._chat_canvas.bind("<Configure>", self._on_chat_canvas_resize)

        # Mouse wheel scrolling
        self._chat_canvas.bind_all("<MouseWheel>",
            lambda e: self._chat_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        # Welcome message
        self._add_chat_bubble(
            "assistant",
            "Welcome to LegalMind Pro. I can search your legal case database, "
            "analyze documents, or just chat. Select a case above to enable RAG search, "
            "or ask me anything.\n\n"
            "You can also attach documents using the paperclip button below for "
            "ad-hoc analysis without ingesting them into a case."
        )

        # Input area at bottom
        input_frame = tk.Frame(chat_frame, bg=BG_MID)
        input_frame.pack(fill="x", side="bottom")

        tk.Frame(chat_frame, bg=BORDER, height=1).pack(fill="x", side="bottom")

        # Attached files indicator
        self._attach_indicator = tk.Frame(input_frame, bg=BG_MID)
        # Hidden initially, shown when files are attached

        input_inner = tk.Frame(input_frame, bg=BG_MID, padx=12, pady=10)
        input_inner.pack(fill="x")

        # Attach button
        attach_btn = tk.Button(
            input_inner, text="\U0001f4ce", font=("Segoe UI", 14),
            bg=BG_MID, fg=FG_DIM, bd=0, padx=4,
            activebackground=BG_MID, activeforeground=ACCENT_LIGHT,
            cursor="hand2", command=self._attach_file)
        attach_btn.pack(side="left", padx=(0, 8))

        # Text input with rounded look
        input_container = tk.Frame(input_inner, bg=BG_ENTRY, bd=0,
                                    highlightthickness=1, highlightcolor=BORDER_GLOW,
                                    highlightbackground=BORDER)
        input_container.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._chat_input = tk.Text(
            input_container, height=2, bg=BG_ENTRY, fg=FG, font=FONT_CHAT,
            insertbackground=FG, wrap="word", bd=0, padx=12, pady=8,
            highlightthickness=0)
        self._chat_input.pack(fill="x", expand=True)
        self._chat_input.bind("<Return>", self._on_chat_enter)
        self._chat_input.bind("<Shift-Return>", lambda e: None)  # allow shift+enter for newline

        # Send button
        send_btn = tk.Button(
            input_inner, text="➤", font=("Segoe UI", 16),
            bg=ACCENT, fg="#ffffff", bd=0, padx=14, pady=4,
            activebackground=ACCENT_HOVER, activeforeground="#ffffff",
            cursor="hand2", command=self._send_chat)
        send_btn.pack(side="right")

        # Quick actions row
        quick_row = tk.Frame(input_frame, bg=BG_MID, padx=12, pady=3)
        quick_row.pack(fill="x")

        for label_text, cmd in [
            ("Copy last", self._copy_last_response),
            ("Export chat", self._export_chat),
            ("Clear chat", self._clear_chat),
        ]:
            btn = tk.Button(quick_row, text=label_text, font=FONT_SMALL,
                            bg=BG_CARD, fg=FG_MUTED, bd=0, padx=8, pady=2,
                            activebackground=BG_HOVER, activeforeground=FG,
                            cursor="hand2", command=cmd)
            btn.pack(side="left", padx=(0, 6))

        # Character counter (right side of quick row)
        self._char_count_label = tk.Label(quick_row, text="0 chars",
                                          font=FONT_SMALL, bg=BG_MID, fg=FG_MUTED)
        self._char_count_label.pack(side="right")
        self._chat_input.bind("<KeyRelease>", self._update_char_count, add="+")

        # Right side: document preview pane (initially hidden)
        self._preview_frame = tk.Frame(self._chat_pane, bg=BG_MID)
        # Don't add to pane yet -- toggled by button

        preview_header = tk.Frame(self._preview_frame, bg=BG_MID)
        preview_header.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(preview_header, text="Document Preview", font=FONT_BOLD,
                 bg=BG_MID, fg=ACCENT_LIGHT).pack(side="left")
        tk.Button(preview_header, text="X", font=FONT_SMALL,
                  bg=BG_MID, fg=FG_DIM, bd=0,
                  command=self._toggle_preview, cursor="hand2"
                  ).pack(side="right")

        self._preview_text = scrolledtext.ScrolledText(
            self._preview_frame, bg=BG_CARD, fg=FG, font=FONT_MONO,
            wrap="word", bd=0, highlightthickness=0, padx=12, pady=8,
            insertbackground=FG, state="disabled")
        self._preview_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Store references for the case combo
        self.query_case_combo = case_combo

    def _update_char_count(self, _event=None):
        try:
            txt = self._chat_input.get("1.0", "end-1c")
            n = len(txt)
            # Rough token estimate: ~4 chars/token for English
            tok = max(1, round(n / 4)) if n else 0
            self._char_count_label.configure(
                text=f"{n} chars  ~{tok} tok" if n else "0 chars")
        except Exception:
            pass

    def _on_chat_canvas_resize(self, event):
        """Resize the chat messages frame to match canvas width."""
        self._chat_canvas.itemconfig(self._chat_canvas_window, width=event.width)

    def _toggle_preview(self):
        if self._preview_visible:
            self._chat_pane.forget(self._preview_frame)
            self._preview_visible = False
            self._preview_btn.configure(text="Doc Preview >>")
        else:
            self._chat_pane.add(self._preview_frame, minsize=300, width=400)
            self._preview_visible = True
            self._preview_btn.configure(text="<< Hide Preview")

    def _show_in_preview(self, text: str, title: str = ""):
        """Display text content in the preview pane."""
        if not self._preview_visible:
            self._toggle_preview()
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")
        if title:
            self._preview_text.insert("end", f"--- {title} ---\n\n")
        self._preview_text.insert("end", text)
        self._preview_text.see("1.0")
        self._preview_text.configure(state="disabled")

    # -- Chat Bubbles ----------------------------------------------------------
    def _resize_msg_text(self, msg_label, max_width=700):
        """Auto-resize a tk.Text inside a bubble to fit its content."""
        try:
            text = msg_label.get("1.0", "end-1c")
            widget_width = msg_label.winfo_width() or max_width - 32
            char_width = 8
            chars_per_line = max(widget_width // char_width, 40)
            total_lines = 0
            for line_text in text.split("\n"):
                total_lines += max(1, (len(line_text) + chars_per_line - 1) // chars_per_line)
            msg_label.configure(height=min(max(total_lines, 1) + 1, 40))
        except Exception:
            pass

    def _add_citation_chips(self, parent, citations: list, bubble_bg: str):
        """Render clickable citation chips below an assistant bubble."""
        if not citations:
            return
        row = tk.Frame(parent, bg=bubble_bg)
        row.pack(anchor="w", pady=(8, 0), fill="x")
        tk.Label(row, text="Sources:", font=FONT_SMALL,
                 bg=bubble_bg, fg=FG_MUTED).pack(side="left", padx=(0, 6))
        # Wrap citations across multiple rows if many
        for c in citations[:8]:
            fname = c.get("filename") or "source"
            page = c.get("page", "?")
            score = c.get("score", 0)
            src = c.get("source") or ""
            label = f"{fname} p.{page}" + (f" ({score:.0%})" if isinstance(score, (int, float)) else "")
            chip = tk.Button(
                row, text=label, font=FONT_SMALL,
                bg=BG_CARD, fg=SKY, bd=0, padx=8, pady=2,
                activebackground=BG_HOVER, activeforeground=ACCENT_LIGHT,
                cursor="hand2",
                command=lambda p=src, pg=page: self._open_source(p, pg))
            chip.pack(side="left", padx=(0, 4), pady=(2, 2))

    def _open_source(self, source_path: str, page=None):
        """Open the source PDF (or other file) at the indicated page if possible."""
        if not source_path:
            self._update_status("No source path on this citation")
            return
        if not os.path.isabs(source_path):
            source_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), source_path)
        if not os.path.exists(source_path):
            messagebox.showinfo(
                "Source not found",
                f"The source file no longer exists:\n{source_path}")
            return
        open_path(source_path)
        self._update_status(
            f"Opened: {os.path.basename(source_path)}"
            + (f" (p.{page})" if page not in (None, "?", "") else ""))

    def _add_chat_bubble(self, role: str, text: str, extra_info: str = "",
                         citations: list | None = None):
        """Add a message bubble to the chat area."""
        is_user = (role == "user")

        # Outer container for alignment
        outer = tk.Frame(self._chat_messages_frame, bg=BG)
        outer.pack(fill="x", padx=16, pady=(4, 4))

        bubble_bg = CHAT_USER_BG if is_user else CHAT_AI_BG
        max_width = 700

        bubble = tk.Frame(outer, bg=bubble_bg, padx=16, pady=10,
                          highlightthickness=1,
                          highlightbackground=BORDER if not is_user else "#3a3a5c")
        if is_user:
            bubble.pack(anchor="e", padx=(80, 0))
        else:
            bubble.pack(anchor="w", padx=(0, 80))

        role_label = "You" if is_user else "LegalMind"
        role_color = ACCENT_LIGHT if is_user else TEAL
        tk.Label(bubble, text=role_label, font=FONT_BOLD,
                 bg=bubble_bg, fg=role_color).pack(anchor="w")

        if extra_info:
            tk.Label(bubble, text=extra_info, font=FONT_SMALL,
                     bg=bubble_bg, fg=FG_MUTED).pack(anchor="w", pady=(0, 4))

        msg_label = tk.Text(bubble, bg=bubble_bg, fg=FG, font=FONT_CHAT,
                            wrap="word", bd=0, highlightthickness=0,
                            padx=0, pady=4, height=1,
                            cursor="arrow")
        msg_label.insert("1.0", text)
        msg_label.configure(state="disabled")
        msg_label.bind("<Configure>", lambda e: self._resize_msg_text(msg_label, max_width))
        msg_label.pack(fill="x", expand=True)

        if not is_user:
            self._add_citation_chips(bubble, citations or [], bubble_bg)

        self._chat_canvas.update_idletasks()
        self._chat_canvas.yview_moveto(1.0)

    def _add_streaming_bubble(self) -> dict:
        """Create an empty assistant bubble that can be filled token-by-token.

        Returns a handle dict with helpers:
            append(text)          -- append text to the bubble (UI thread)
            set_extra(text)       -- set/replace the small info line
            attach_citations(list)-- render clickable source chips
            destroy()             -- remove the bubble (e.g. on early error)
            text()                -- current accumulated text
        """
        bubble_bg = CHAT_AI_BG
        max_width = 700

        outer = tk.Frame(self._chat_messages_frame, bg=BG)
        outer.pack(fill="x", padx=16, pady=(4, 4))
        bubble = tk.Frame(outer, bg=bubble_bg, padx=16, pady=10,
                          highlightthickness=1, highlightbackground=BORDER)
        bubble.pack(anchor="w", padx=(0, 80))

        tk.Label(bubble, text="LegalMind", font=FONT_BOLD,
                 bg=bubble_bg, fg=TEAL).pack(anchor="w")
        info_label = tk.Label(bubble, text="...", font=FONT_SMALL,
                              bg=bubble_bg, fg=FG_MUTED)
        info_label.pack(anchor="w", pady=(0, 4))

        msg = tk.Text(bubble, bg=bubble_bg, fg=FG, font=FONT_CHAT,
                      wrap="word", bd=0, highlightthickness=0,
                      padx=0, pady=4, height=1, cursor="arrow")
        msg.configure(state="disabled")
        msg.pack(fill="x", expand=True)

        accumulator = []

        def append(text: str):
            if not text:
                return
            accumulator.append(text)
            try:
                msg.configure(state="normal")
                msg.insert("end", text)
                msg.configure(state="disabled")
                self._resize_msg_text(msg, max_width)
                self._chat_canvas.update_idletasks()
                self._chat_canvas.yview_moveto(1.0)
            except Exception:
                pass

        def set_extra(s: str):
            try:
                info_label.configure(text=s or " ")
            except Exception:
                pass

        def attach_citations(cits: list):
            try:
                self._add_citation_chips(bubble, cits or [], bubble_bg)
                self._chat_canvas.update_idletasks()
                self._chat_canvas.yview_moveto(1.0)
            except Exception:
                pass

        def destroy():
            try:
                outer.destroy()
            except Exception:
                pass

        def text():
            return "".join(accumulator)

        return {
            "append": append,
            "set_extra": set_extra,
            "attach_citations": attach_citations,
            "destroy": destroy,
            "text": text,
        }

    def _add_rag_indicator(self, case_name: str, num_results: int, top_score: float):
        """Add a subtle RAG context indicator in the chat."""
        outer = tk.Frame(self._chat_messages_frame, bg=BG)
        outer.pack(fill="x", padx=16, pady=(2, 2))

        indicator = tk.Frame(outer, bg=CHAT_RAG_BG, padx=12, pady=4,
                             highlightthickness=1, highlightbackground="#1a3540")
        indicator.pack(anchor="w", padx=(0, 200))

        score_str = f"{top_score:.0%}" if isinstance(top_score, (int, float)) else str(top_score)
        tk.Label(indicator,
                 text=f"Searched '{case_name}' -- {num_results} results (top: {score_str})",
                 font=FONT_SMALL, bg=CHAT_RAG_BG, fg=SKY).pack(side="left")

    def _add_rag_error_indicator(self, case_name: str, message: str):
        """Warn that the document search failed, rather than saying nothing.

        Without this the answer below would be generated with no documents
        attached and no sign anything went wrong -- indistinguishable from
        'this case contains nothing relevant'.
        """
        outer = tk.Frame(self._chat_messages_frame, bg=BG)
        outer.pack(fill="x", padx=16, pady=(2, 2))

        indicator = tk.Frame(outer, bg=CHAT_RAG_BG, padx=12, pady=4,
                             highlightthickness=1, highlightbackground="#5a1a1a")
        indicator.pack(anchor="w", padx=(0, 120))

        tk.Label(indicator,
                 text=(f"Document search FAILED for '{case_name}' -- "
                       f"answering WITHOUT case documents. {message[:120]}"),
                 font=FONT_SMALL, bg=CHAT_RAG_BG, fg="#ff6b6b",
                 wraplength=560, justify="left").pack(side="left")

    # -- Chat Input Handling ---------------------------------------------------
    def _on_chat_enter(self, event):
        """Handle Enter key in chat input (send message)."""
        # Shift+Enter adds newline, plain Enter sends
        if not event.state & 0x1:  # not Shift
            self._send_chat()
            return "break"

    def _send_chat(self):
        """Send the current chat input."""
        text = self._chat_input.get("1.0", "end").strip()
        if not text:
            return

        self._chat_input.delete("1.0", "end")
        self._update_char_count()

        # Add user bubble
        attach_info = ""
        if self._attached_files:
            names = ", ".join(f["name"] for f in self._attached_files)
            attach_info = f"Attached: {names}"
        self._add_chat_bubble("user", text, extra_info=attach_info)

        # Store in history
        self._chat_history.append({"role": "user", "content": text})

        # Read tkinter vars on main thread
        case = self.query_case_var.get().strip()
        provider = self.llm_provider_var.get()
        analysis_mode_name = self.analysis_mode_var.get()
        attached = self._attached_files.copy()
        self._attached_files.clear()
        self._update_attach_indicator()

        # Resolve analysis mode
        analysis_mode = "standard"
        for key, val in ANALYSIS_MODES.items():
            if val["name"] == analysis_mode_name:
                analysis_mode = key
                break

        # Resolve model + behavior settings
        saved_settings = _load_settings()
        if provider == "claude":
            model = saved_settings.get("claude_model", DEFAULT_CLAUDE_MODEL)
        elif provider == "openai":
            model = saved_settings.get("openai_model", DEFAULT_OPENAI_MODEL)
        else:
            model = saved_settings.get("ollama_model", DEFAULT_OLLAMA_MODEL)

        use_stream = bool(saved_settings.get("streaming_enabled", True))

        # History snapshot (excluding the just-added user message; we add it
        # explicitly so callers can reorder it relative to RAG context).
        prior_history = list(self._chat_history[:-1])

        if use_stream:
            handle = self._add_streaming_bubble()
            handle["set_extra"]("Working...")

            def on_token(t: str):
                # _generate_response runs in a background thread; marshal
                # token append back to the UI thread.
                try:
                    self.root.after(0, handle["append"], t)
                except Exception:
                    pass

            def run():
                try:
                    response_text, extra, citations = self._generate_response(
                        text, case, provider, model, analysis_mode, attached,
                        prior_history=prior_history, on_token=on_token)

                    def finalize():
                        if not handle["text"]() and response_text:
                            handle["append"](response_text)
                        handle["set_extra"](extra or " ")
                        handle["attach_citations"](citations)
                        self._chat_history.append(
                            {"role": "assistant", "content": response_text})
                        self._autosave_session()
                    self.root.after(0, finalize)
                except Exception as e:
                    logger.error("Chat error: %s", e, exc_info=True)
                    def show_err():
                        handle["destroy"]()
                        self._add_chat_bubble(
                            "assistant", f"Sorry, an error occurred: {e}",
                            extra_info="Error")
                    self.root.after(0, show_err)

            threading.Thread(target=run, daemon=True).start()
            return

        # Non-streaming: keep the classic "Thinking..." indicator
        thinking_frame = tk.Frame(self._chat_messages_frame, bg=BG)
        thinking_frame.pack(fill="x", padx=16, pady=(4, 4))
        thinking_bubble = tk.Frame(thinking_frame, bg=CHAT_AI_BG, padx=16, pady=8,
                                    highlightthickness=1, highlightbackground=BORDER)
        thinking_bubble.pack(anchor="w", padx=(0, 80))
        tk.Label(thinking_bubble, text="Thinking...", font=FONT,
                 bg=CHAT_AI_BG, fg=FG_MUTED).pack()
        self._chat_canvas.update_idletasks()
        self._chat_canvas.yview_moveto(1.0)

        def run():
            try:
                response_text, extra, citations = self._generate_response(
                    text, case, provider, model, analysis_mode, attached,
                    prior_history=prior_history, on_token=None)

                def show():
                    thinking_frame.destroy()
                    self._add_chat_bubble(
                        "assistant", response_text,
                        extra_info=extra, citations=citations)
                    self._chat_history.append(
                        {"role": "assistant", "content": response_text})
                    self._autosave_session()
                self.root.after(0, show)
            except Exception as e:
                logger.error("Chat error: %s", e, exc_info=True)
                def show_err():
                    thinking_frame.destroy()
                    self._add_chat_bubble("assistant",
                        f"Sorry, an error occurred: {e}",
                        extra_info="Error")
                self.root.after(0, show_err)

        threading.Thread(target=run, daemon=True).start()

    def _generate_response(self, query, case, provider, model, analysis_mode,
                           attached, prior_history=None, on_token=None):
        """Generate a response (runs in background thread).

        Returns ``(response_text, extra_info, citations)``.

        ``on_token`` is an optional callable invoked with each streamed text
        chunk; when provided, the streaming variant of the LLM is used.

        ``prior_history`` is the conversation up to (but not including) this
        turn's user message. When the ``multi_turn_history`` setting is on
        AND the provider is Claude/OpenAI, recent turns are sent alongside
        the new user content. Ollama always flattens history into a single
        prompt regardless.
        """
        context_parts = []
        extra_info = ""
        rag_used = False
        citations = []  # [{filename, source, page, score}, ...]

        # Include attached file content
        if attached:
            for f in attached:
                context_parts.append(f"[Attached: {f['name']}]:\n{f['text'][:8000]}")
            extra_info = f"Analyzed {len(attached)} attached file(s)"

        # Auto-detect RAG: search the case if one is selected
        rag_results = []
        if case:
            try:
                logger.info("Auto-detect: searching case '%s' for: '%s'", case, query[:60])
                rag_results = query_collection(
                    case_name=case, query_text=query, k=6)

                # Filter EVERY result, not just the top one. Gating on the
                # top score alone injected all k results whenever any one of
                # them was good, so weak matches rode in alongside strong ones.
                rag_results = [r for r in rag_results
                               if r.get("score", 0) >= RELEVANCE_GATE]

                if rag_results:
                    top_score = rag_results[0].get("score", 0)
                    rag_used = True
                    self.root.after(0, self._add_rag_indicator,
                                    case, len(rag_results), top_score)
                    for r in rag_results:
                        src = r.get("filename", "unknown")
                        pg = r.get("page", "?")
                        score = r.get("score", 0)
                        content = r.get("content", "")
                        context_parts.append(
                            f"[{src} p.{pg} | relevance: {score:.0%}]:\n{content}")
                        citations.append({
                            "filename": src,
                            "source": r.get("source", ""),
                            "page": pg,
                            "score": score,
                        })

                    # Build preview pane content
                    preview_text = ""
                    for i, r in enumerate(rag_results[:5], 1):
                        fname = r.get("filename", "unknown")
                        pg = r.get("page", "?")
                        dtype = r.get("doc_type", "")
                        score = r.get("score", 0)
                        content = (r.get("content") or "")[:400]
                        preview_text += (
                            f"--- Result {i} ({score:.0%}) ---\n"
                            f"Source: {fname} | Page: {pg}"
                            f"{' | ' + dtype if dtype else ''}\n\n"
                            f"{content}\n\n"
                        )
                    self.root.after(0, self._show_in_preview, preview_text,
                                    f"RAG Results for: {query[:40]}")

                    rag_info = f"RAG: {len(rag_results)} results from '{case}'"
                    extra_info = f"{extra_info} | {rag_info}" if extra_info else rag_info
                else:
                    logger.info("Auto-detect: no result reached the %.2f "
                                "relevance gate; answering without case "
                                "documents.", RELEVANCE_GATE)

            except Exception as e:
                # Surface this. A silent "no results" when the index is
                # unreadable would let the user conclude the documents do
                # not exist, which in a legal-evidence tool is worse than
                # showing an error.
                logger.error("RAG search failed for case '%s': %s",
                             case, e, exc_info=True)
                self.root.after(
                    0, self._add_rag_error_indicator, case, str(e))

        # Build the user-turn content (the message we'll send THIS turn)
        if context_parts:
            context = "\n\n".join(context_parts)
            if rag_used:
                user_content = get_analysis_prompt(analysis_mode, context, query)
            else:
                user_content = (
                    "You are a helpful legal assistant. Use the following document "
                    "content to answer the question. If the answer is not in the "
                    "documents, say so.\n\n"
                    f"DOCUMENTS:\n{context}\n\n"
                    f"QUESTION:\n{query}\n\nANSWER:"
                )
        else:
            user_content = query
            if not extra_info:
                extra_info = f"General chat via {provider}"

        system_prompt = (
            "You are LegalMind Pro, a senior legal research assistant. "
            "Be precise, cite sources when given evidence, and say "
            "\"Evidence not found.\" when the answer is not in the provided "
            "context. Otherwise answer general legal questions clearly."
        )

        # Build multi-turn messages (Claude/OpenAI) or fallback prompt (Ollama)
        settings = _load_settings()
        multi_turn = bool(settings.get("multi_turn_history", False))
        depth = max(2, int(settings.get("history_depth", 6)))
        api_kwargs = {}
        if provider == "openai":
            api_kwargs["url"] = settings.get("openai_url", DEFAULT_OPENAI_URL)

        messages = []
        if multi_turn and provider in ("claude", "openai", "claude-cli") and prior_history:
            # Trim history to the configured depth, keeping it well-formed
            trimmed = prior_history[-depth:]
            # Drop a leading assistant turn if it ends up alone
            while trimmed and trimmed[0].get("role") != "user":
                trimmed = trimmed[1:]
            messages.extend(trimmed)
        messages.append({"role": "user", "content": user_content})

        # Single-turn flat prompt (used by Ollama and non-streaming fallback)
        flat_prompt = user_content

        try:
            if on_token is not None:
                logger.info("Streaming from %s (model=%s, multi-turn=%s, msgs=%d)",
                            provider, model, multi_turn, len(messages))
                response = query_llm_stream(
                    provider, on_token, model=model,
                    prompt=flat_prompt, messages=messages,
                    system=system_prompt, **api_kwargs)
            else:
                logger.info("Sending to %s (model=%s, multi-turn=%s)",
                            provider, model, multi_turn)
                if multi_turn and provider == "claude":
                    response = query_claude_messages(
                        messages, model=model or DEFAULT_CLAUDE_MODEL,
                        system=system_prompt)
                elif multi_turn and provider == "openai":
                    response = query_openai_messages(
                        messages, model=model or DEFAULT_OPENAI_MODEL,
                        url=api_kwargs.get("url", DEFAULT_OPENAI_URL))
                elif multi_turn and provider == "claude-cli" and len(messages) > 1:
                    # CLI print mode is single-shot: flatten history into stdin.
                    parts = ([system_prompt] if system_prompt else [])
                    for m in messages:
                        who = "User" if m.get("role") == "user" else "Assistant"
                        parts.append(f"{who}: {m.get('content', '')}")
                    response = query_llm("\n\n".join(parts), provider=provider,
                                         model=model)
                else:
                    response = query_llm(flat_prompt, provider=provider, model=model)
        except Exception as e:
            logger.error("LLM call failed: %s", e, exc_info=True)
            response = f"[Error talking to {provider}: {e}]"

        logger.info("Response: %d chars", len(response) if response else 0)
        if not citations and rag_used:
            # Defensive: should not happen, but keeps types consistent
            citations = []
        return (response.strip() if response else "No response generated."), extra_info, citations

    # -- File Attachment -------------------------------------------------------
    def _attach_file(self):
        """Open file dialog to attach a document for ad-hoc analysis."""
        paths = filedialog.askopenfilenames(
            title="Attach Documents",
            filetypes=[
                ("All supported", "*.pdf *.txt *.md *.docx"),
                ("PDF files", "*.pdf"),
                ("Text files", "*.txt *.md"),
                ("Word documents", "*.docx"),
                ("All files", "*.*"),
            ])
        if not paths:
            return

        for path in paths:
            fname = os.path.basename(path)
            ext = os.path.splitext(fname)[1].lower()

            try:
                if ext == ".pdf":
                    text = self._extract_pdf_text(path)
                elif ext == ".docx":
                    text = self._extract_docx_text(path)
                else:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()

                self._attached_files.append({
                    "name": fname, "text": text, "path": path})
                self._update_status(f"Attached: {fname} ({len(text):,} chars)")
                logger.info("Attached file: %s (%d chars)", fname, len(text))

            except Exception as e:
                logger.error("Failed to read %s: %s", fname, e)
                messagebox.showerror("Attach Error", f"Could not read {fname}:\n{e}")

        self._update_attach_indicator()

    def _extract_pdf_text(self, path):
        import fitz
        text = ""
        with fitz.open(path) as doc:
            for page in doc:
                text += page.get_text("text") + "\n"
        return text

    def _extract_docx_text(self, path):
        try:
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            return "(python-docx not installed -- cannot read .docx files)"

    def _update_attach_indicator(self):
        """Update the attached files indicator in the chat input area."""
        # Clear existing indicator
        for w in self._attach_indicator.winfo_children():
            w.destroy()

        if self._attached_files:
            self._attach_indicator.pack(fill="x", padx=12, pady=(6, 0),
                                         before=self._attach_indicator.master.winfo_children()[-1])
            for i, f in enumerate(self._attached_files):
                chip = tk.Frame(self._attach_indicator, bg=BG_CARD, padx=8, pady=2)
                chip.pack(side="left", padx=(0, 4))
                tk.Label(chip, text=f["name"], font=FONT_SMALL,
                         bg=BG_CARD, fg=SKY).pack(side="left")
                tk.Button(chip, text="x", font=FONT_SMALL, bg=BG_CARD, fg=ROSE,
                          bd=0, padx=4, cursor="hand2",
                          command=lambda idx=i: self._remove_attachment(idx)
                          ).pack(side="left", padx=(4, 0))
        else:
            self._attach_indicator.pack_forget()

    def _remove_attachment(self, idx):
        if 0 <= idx < len(self._attached_files):
            removed = self._attached_files.pop(idx)
            self._update_status(f"Removed: {removed['name']}")
            self._update_attach_indicator()

    # -- Chat Actions ----------------------------------------------------------
    def _copy_last_response(self):
        for msg in reversed(self._chat_history):
            if msg["role"] == "assistant":
                self.root.clipboard_clear()
                self.root.clipboard_append(msg["content"])
                self._update_status("Last response copied to clipboard")
                return
        self._update_status("No response to copy")

    def _export_chat(self):
        if not self._chat_history:
            messagebox.showinfo("Empty", "No chat history to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Chat",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("Markdown", "*.md")],
            initialfile=f"legalmind_chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for msg in self._chat_history:
                    role = "You" if msg["role"] == "user" else "LegalMind"
                    f.write(f"[{role}]\n{msg['content']}\n\n")
            self._update_status(f"Chat exported to {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    def _clear_chat(self):
        if self._chat_history and not messagebox.askyesno(
                "Clear Chat", "Clear all chat messages?"):
            return
        for w in self._chat_messages_frame.winfo_children():
            w.destroy()
        self._chat_history.clear()
        self._current_session_path = None
        self._add_chat_bubble(
            "assistant",
            "Chat cleared. How can I help you?")

    # -- Persistent Chat Sessions ---------------------------------------------
    def _autosave_session(self):
        """Auto-save the current conversation to chat_sessions/.

        Skips silently if autosave is off or there is nothing to save.
        Each chat gets its own file the first time it's saved (named by
        timestamp + first-user-message snippet); subsequent saves overwrite
        that same file so the chat appears once in the Recent list.
        """
        if not _load_settings().get("chat_autosave", True):
            return
        if not self._chat_history:
            return
        try:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            if not getattr(self, "_current_session_path", None):
                # Build a filename from the first user message
                first_user = next(
                    (m["content"] for m in self._chat_history
                     if m.get("role") == "user"), "chat")
                slug = "".join(
                    c if c.isalnum() or c in " _-" else " "
                    for c in first_user[:40]).strip().replace(" ", "_") or "chat"
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._current_session_path = os.path.join(
                    SESSIONS_DIR, f"{ts}__{slug}.json")
            data = {
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "case": self.query_case_var.get(),
                "provider": self.llm_provider_var.get(),
                "mode": self.analysis_mode_var.get(),
                "history": self._chat_history,
            }
            with open(self._current_session_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug("autosave_session failed: %s", e)

    def _list_sessions(self) -> list[dict]:
        """Return recent sessions, newest first."""
        if not os.path.isdir(SESSIONS_DIR):
            return []
        out = []
        for name in os.listdir(SESSIONS_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(SESSIONS_DIR, name)
            try:
                mtime = os.path.getmtime(path)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                first_user = next(
                    (m["content"] for m in data.get("history", [])
                     if m.get("role") == "user"), "(no messages)")
                out.append({
                    "path": path,
                    "name": name,
                    "mtime": mtime,
                    "title": (first_user[:60] + ("…" if len(first_user) > 60 else "")),
                    "case": data.get("case", ""),
                    "turns": sum(1 for m in data.get("history", [])
                                 if m.get("role") == "user"),
                })
            except Exception:
                continue
        out.sort(key=lambda d: d["mtime"], reverse=True)
        return out

    def _show_sessions_menu(self):
        """Pop up a Sessions menu next to the Sessions button."""
        menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=FG,
                       activebackground=BG_HOVER, activeforeground=FG,
                       bd=0)
        menu.add_command(label="New chat", command=self._new_session)
        menu.add_command(label="Save chat as...", command=self._save_session_as)
        menu.add_command(label="Open sessions folder...",
                         command=self._open_sessions_folder)
        menu.add_separator()

        sessions = self._list_sessions()
        if not sessions:
            menu.add_command(label="(no saved chats yet)", state="disabled")
        else:
            menu.add_command(label="Recent chats:", state="disabled")
            for s in sessions[:15]:
                ts = datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
                case = f" [{s['case']}]" if s.get("case") else ""
                label = f"  {ts}{case}  {s['title']}"
                menu.add_command(
                    label=label,
                    command=lambda p=s["path"]: self._load_session(p))
            if len(sessions) > 15:
                menu.add_command(
                    label=f"  ... {len(sessions) - 15} older (open folder)",
                    command=self._open_sessions_folder)

        try:
            x = self._sessions_btn.winfo_rootx()
            y = self._sessions_btn.winfo_rooty() + self._sessions_btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _new_session(self):
        """Start a fresh chat (saves current first if non-empty)."""
        if self._chat_history:
            self._autosave_session()
        for w in self._chat_messages_frame.winfo_children():
            w.destroy()
        self._chat_history.clear()
        self._current_session_path = None
        self._add_chat_bubble(
            "assistant", "New chat started. How can I help?")
        self._update_status("Started a new chat session")

    def _save_session_as(self):
        """Export the current chat to a chosen .json path."""
        if not self._chat_history:
            messagebox.showinfo("Empty", "No messages to save.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Chat Session",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialdir=SESSIONS_DIR if os.path.isdir(SESSIONS_DIR) else ".",
            initialfile=f"legalmind_chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        if not path:
            return
        try:
            data = {
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "case": self.query_case_var.get(),
                "provider": self.llm_provider_var.get(),
                "mode": self.analysis_mode_var.get(),
                "history": self._chat_history,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._current_session_path = path
            self._update_status(f"Saved chat to {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _load_session(self, path: str):
        """Replace the current chat with a saved session from disk."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Load failed", f"Could not read session:\n{e}")
            return

        if self._chat_history:
            self._autosave_session()

        for w in self._chat_messages_frame.winfo_children():
            w.destroy()
        self._chat_history.clear()
        self._current_session_path = path

        # Restore selectors
        if data.get("case"):
            self.query_case_var.set(data["case"])
        if data.get("provider"):
            self.llm_provider_var.set(data["provider"])
        if data.get("mode"):
            self.analysis_mode_var.set(data["mode"])

        history = data.get("history", [])
        for m in history:
            role = m.get("role", "assistant")
            content = m.get("content", "")
            self._chat_history.append({"role": role, "content": content})
            self._add_chat_bubble(role, content)
        self._update_status(
            f"Loaded chat: {os.path.basename(path)} ({len(history)} messages)")

    def _open_sessions_folder(self):
        try:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            open_path(SESSIONS_DIR)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))

    # =========================================================================
    # INGEST TAB (with pause/stop)
    # =========================================================================
    def _build_ingest_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Ingest  ")

        # Top card: case + folder selection
        top_card = tk.Frame(tab, bg=BG_CARD, highlightthickness=1,
                            highlightbackground=BORDER)
        top_card.pack(fill="x", padx=20, pady=(16, 8))

        top_inner = tk.Frame(top_card, bg=BG_CARD, padx=16, pady=12)
        top_inner.pack(fill="x")

        # Case row
        case_row = tk.Frame(top_inner, bg=BG_CARD)
        case_row.pack(fill="x", pady=(0, 8))

        tk.Label(case_row, text="Case:", font=FONT_BOLD, bg=BG_CARD, fg=FG
                 ).pack(side="left")
        self.ingest_case_var = tk.StringVar()
        self.ingest_case_combo = ttk.Combobox(
            case_row, textvariable=self.ingest_case_var, width=25)
        self.ingest_case_combo.pack(side="left", padx=(8, 12))
        tk.Label(case_row, text="pick existing or type new",
                 font=FONT_SMALL, bg=BG_CARD, fg=FG_MUTED).pack(side="left")

        # Folder row
        folder_row = tk.Frame(top_inner, bg=BG_CARD)
        folder_row.pack(fill="x")

        tk.Label(folder_row, text="Folder:", font=FONT_BOLD, bg=BG_CARD, fg=FG
                 ).pack(side="left")
        self.ingest_folder_var = tk.StringVar()
        folder_entry = ttk.Entry(folder_row, textvariable=self.ingest_folder_var,
                                  width=50, state="readonly")
        folder_entry.pack(side="left", padx=(8, 8), fill="x", expand=True)
        ttk.Button(folder_row, text="Browse...",
                   command=self._browse_ingest_folder).pack(side="left")

        # Options row
        opts_card = tk.Frame(tab, bg=BG_CARD, highlightthickness=1,
                             highlightbackground=BORDER)
        opts_card.pack(fill="x", padx=20, pady=(0, 8))

        opts_inner = tk.Frame(opts_card, bg=BG_CARD, padx=16, pady=8)
        opts_inner.pack(fill="x")

        self.ocr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts_inner, text="Enable OCR",
                        variable=self.ocr_var).pack(side="left")
        tk.Label(opts_inner, text="Chunk size:", font=FONT,
                 bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(20, 4))
        self.chunk_size_var = tk.StringVar(value="1200")
        ttk.Entry(opts_inner, textvariable=self.chunk_size_var, width=6
                  ).pack(side="left")

        # ── Background throttle: the switch + slider ─────────────────────
        _saved = _load_settings()
        # A widget's own padx/pady must be a single distance; the asymmetric
        # (top, bottom) spacing belongs on the geometry call (.pack), not here.
        thr_inner = tk.Frame(opts_card, bg=BG_CARD, padx=16, pady=4)
        thr_inner.pack(fill="x", pady=(0, 8))

        self.bg_ingest_var = tk.BooleanVar(
            value=bool(_saved.get("ingest_background", False)))
        ttk.Checkbutton(thr_inner, text="Background mode (use spare CPU only)",
                        variable=self.bg_ingest_var,
                        command=self._save_throttle_settings).pack(side="left")

        tk.Label(thr_inner, text="Leave free:", font=FONT,
                 bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(20, 4))
        self.cpu_headroom_var = tk.IntVar(
            value=int(_saved.get("ingest_cpu_headroom", 50)))
        self.cpu_headroom_label = tk.Label(
            thr_inner, text=f"{self.cpu_headroom_var.get()}% CPU",
            font=FONT, bg=BG_CARD, fg=FG, width=8, anchor="w")
        ttk.Scale(thr_inner, from_=10, to=90, orient="horizontal", length=160,
                  variable=self.cpu_headroom_var,
                  command=self._on_headroom_change).pack(side="left", padx=(0, 6))
        self.cpu_headroom_label.pack(side="left")

        # Control buttons: Start | Pause | Stop
        btn_card = tk.Frame(tab, bg=BG_CARD, highlightthickness=1,
                            highlightbackground=BORDER)
        btn_card.pack(fill="x", padx=20, pady=(0, 8))
        btn_inner = tk.Frame(btn_card, bg=BG_CARD, padx=16, pady=10)
        btn_inner.pack(fill="x")

        self.ingest_btn = ttk.Button(btn_inner, text="▶  Start Ingestion",
                                     style="Accent.TButton", command=self._start_ingest)
        self.ingest_btn.pack(side="left", padx=(0, 8))

        self.pause_btn = ttk.Button(btn_inner, text="⏸  Pause",
                                    style="Amber.TButton", command=self._toggle_pause,
                                    state="disabled")
        self.pause_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ttk.Button(btn_inner, text="⏹  Stop",
                                   style="Danger.TButton", command=self._stop_ingest,
                                   state="disabled")
        self.stop_btn.pack(side="left")

        # Progress area
        prog_card = tk.Frame(tab, bg=BG_CARD, highlightthickness=1,
                             highlightbackground=BORDER)
        prog_card.pack(fill="x", padx=20, pady=(0, 8))
        prog_inner = tk.Frame(prog_card, bg=BG_CARD, padx=16, pady=10)
        prog_inner.pack(fill="x")

        self.progress_label = tk.Label(prog_inner, text="Ready.",
                                        font=FONT, bg=BG_CARD, fg=FG_DIM, anchor="w")
        self.progress_label.pack(fill="x", pady=(0, 4))

        self.file_progress = ttk.Progressbar(prog_inner, mode="determinate",
                                              style="Teal.Horizontal.TProgressbar")
        self.file_progress.pack(fill="x", pady=(0, 4))

        self.page_label = tk.Label(prog_inner, text="", font=FONT_SMALL,
                                    bg=BG_CARD, fg=FG_MUTED, anchor="w")
        self.page_label.pack(fill="x")

        # Log pane
        log_card = tk.Frame(tab, bg=BG_CARD, highlightthickness=1,
                            highlightbackground=BORDER)
        log_card.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        log_header = tk.Frame(log_card, bg=BG_CARD, padx=16, pady=4)
        log_header.pack(fill="x")
        tk.Label(log_header, text="Log", font=FONT_BOLD, bg=BG_CARD, fg=ACCENT_LIGHT
                 ).pack(side="left")

        self.log_text = scrolledtext.ScrolledText(
            log_card, height=10, bg=BG, fg=FG, font=FONT_MONO,
            insertbackground=FG, wrap="word", borderwidth=0, highlightthickness=0,
            state="disabled", padx=12, pady=8)
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.log_text.tag_configure("info", foreground=FG)
        self.log_text.tag_configure("success", foreground=TEAL)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("error", foreground=ROSE)
        self.log_text.tag_configure("accent", foreground=ACCENT_LIGHT)
        self.log_text.tag_configure("paused", foreground=AMBER)

    def _browse_ingest_folder(self):
        folder = filedialog.askdirectory(title="Select folder containing PDFs")
        if folder:
            self.ingest_folder_var.set(folder)
            pdfs = discover_pdfs(folder)
            self._log(f"Found {len(pdfs)} PDF(s) in {folder}", "accent")

    def _log(self, msg: str, tag: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_headroom_change(self, _value):
        """Live-update the slider's label and persist."""
        pct = int(float(_value))
        self.cpu_headroom_label.configure(text=f"{pct}% CPU")
        self._save_throttle_settings()

    def _save_throttle_settings(self):
        """Persist the background switch + slider to settings.json."""
        try:
            save_settings({
                "ingest_background": bool(self.bg_ingest_var.get()),
                "ingest_cpu_headroom": int(self.cpu_headroom_var.get()),
            })
        except Exception as e:
            logger.warning("could not save throttle settings: %s", e)

    def _toggle_pause(self):
        """Toggle pause state."""
        if self._pause_ingest:
            # Resume
            self._pause_ingest = False
            self.pause_btn.configure(text="⏸  Pause")
            self._log("Resuming ingestion...", "accent")
        else:
            # Pause
            self._pause_ingest = True
            self.pause_btn.configure(text="▶  Resume")
            self._log("Pausing ingestion (will pause after current file)...", "warn")

    def _stop_ingest(self):
        """Stop ingestion, preserving all already-embedded chunks."""
        self._cancel_ingest = True
        self._pause_ingest = False  # unpause if paused so the loop can exit
        self._log("Stop requested -- saving progress...", "warn")

    def _start_ingest(self):
        case = self.ingest_case_var.get().strip()
        folder = self.ingest_folder_var.get().strip()
        if not case:
            messagebox.showwarning("Missing Case Name", "Please enter a case name.")
            return
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing Folder", "Please select a valid folder.")
            return

        try:
            chunk_size = int(self.chunk_size_var.get())
        except ValueError:
            chunk_size = 1200

        use_ocr = self.ocr_var.get()
        chunk_overlap = max(chunk_size // 6, 100)

        # Build the throttle from the current switch + slider state.
        import throttle as _throttle
        bg = self.bg_ingest_var.get()
        headroom = int(self.cpu_headroom_var.get())
        ingest_throttle = _throttle.CpuThrottle(
            headroom_pct=headroom, enabled=bg)

        self._cancel_ingest = False
        self._pause_ingest = False
        self._ingest_running = True
        self.ingest_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal")
        self.stop_btn.configure(state="normal")
        self.file_progress["value"] = 0
        self.page_label.configure(text="")

        self._log("=" * 45, "accent")
        self._log("INGEST STARTED", "accent")
        self._log(f"  Case:       {case}", "accent")
        self._log(f"  Folder:     {folder}", "accent")
        self._log(f"  Chunk size: {chunk_size}", "accent")
        self._log(f"  OCR:        {'ON' if use_ocr else 'OFF'}", "accent")
        self._log(f"  Background: {('ON (leave ' + str(headroom) + '% CPU free)') if bg else 'OFF'}",
                  "accent")
        self._log("=" * 45, "accent")

        logger.info("Launching ingest thread: case=%s, folder=%s, chunk_size=%d, "
                    "ocr=%s, background=%s headroom=%d%%",
                    case, folder, chunk_size, use_ocr, bg, headroom)

        def run():
            try:
                result = ingest_folder(
                    folder, case,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    use_ocr=use_ocr,
                    progress_cb=self._on_ingest_progress,
                    cancel_flag=lambda: self._cancel_ingest,
                    pause_flag=lambda: self._pause_ingest,
                    throttle=ingest_throttle,
                )
                self.root.after(0, self._on_ingest_done, result)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                logger.critical("INGEST THREAD CRASH: %s\n%s", e, tb)
                self.root.after(0, self._log, f"FATAL INGEST ERROR: {e}", "error")
                self.root.after(0, self._on_ingest_done,
                                IngestResult(case_name=case, errors=[str(e)]))

        threading.Thread(target=run, daemon=True, name="ingest-worker").start()

    def _on_ingest_progress(self, p: IngestProgress):
        def update():
            self.progress_label.configure(text=p.message)

            if p.total_files > 0 and p.file_index > 0:
                pct = (p.file_index / p.total_files) * 100
                self.file_progress["value"] = pct

            if p.total_pages > 0:
                self.page_label.configure(
                    text=f"  Page {p.page_index}/{p.total_pages} of {p.current_file}")
            elif p.phase == "embedding" and p.chunks_so_far > 0:
                self.page_label.configure(text=f"  Chunks stored: {p.chunks_so_far}")
            elif p.phase == "paused":
                self.page_label.configure(text=f"  PAUSED -- chunks saved: {p.chunks_so_far}")
            elif p.phase == "scanning":
                self.page_label.configure(text="  Scanning...")

            tag = {
                "scanning":   "accent",
                "extracting": "info",
                "chunking":   "info",
                "embedding":  "warn",
                "paused":     "paused",
                "done":       "success",
                "error":      "error",
            }.get(p.phase, "info")
            self._log(p.message, tag)
        self.root.after(0, update)

    def _on_ingest_done(self, result: IngestResult):
        self._ingest_running = False
        self._pause_ingest = False
        self.ingest_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="⏸  Pause")
        self.stop_btn.configure(state="disabled")
        self.file_progress["value"] = 100 if result.files_processed > 0 else 0

        self._log("=" * 45, "success")
        self._log("INGEST COMPLETE", "success")
        self._log(f"  Files processed: {result.files_processed}", "success")
        self._log(f"  Files skipped:   {result.files_skipped} (already indexed)", "info")
        self._log(f"  Total chunks:    {result.total_chunks}", "success")
        self._log(f"  Time elapsed:    {result.elapsed:.1f}s", "info")
        if result.errors:
            self._log(f"  Errors:          {len(result.errors)}", "error")
            for err in result.errors:
                self._log(f"    - {err}", "error")
        self._log("=" * 45, "success")

        self._refresh_case_lists()

    # =========================================================================
    # CASES TAB
    # =========================================================================
    def _build_cases_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Cases  ")

        top = tk.Frame(tab, bg=BG)
        top.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(top, text="Indexed Cases", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).pack(side="left")
        ttk.Button(top, text="Refresh", style="Ghost.TButton",
                   command=self._refresh_case_lists).pack(side="right")

        # Actions
        actions = tk.Frame(tab, bg=BG)
        actions.pack(fill="x", padx=20, pady=(0, 8))
        for text_label, cmd, sty in [
            ("Rename", self._rename_case, "TButton"),
            ("Clone", self._clone_case, "TButton"),
            ("Delete", self._delete_case, "Danger.TButton"),
        ]:
            ttk.Button(actions, text=text_label, style=sty,
                       command=cmd).pack(side="left", padx=(0, 8))

        # Treeview in a card
        tree_card = tk.Frame(tab, bg=BG_CARD, highlightthickness=1,
                             highlightbackground=BORDER)
        tree_card.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        cols = ("name", "chunks")
        self.cases_tree = ttk.Treeview(tree_card, columns=cols, show="headings", height=12)
        self.cases_tree.heading("name", text="Case Name")
        self.cases_tree.heading("chunks", text="Chunks")
        self.cases_tree.column("name", width=350)
        self.cases_tree.column("chunks", width=120, anchor="center")
        self.cases_tree.pack(fill="both", expand=True, padx=4, pady=4)

        self.cases_status = tk.Label(tab, text="", font=FONT, bg=BG, fg=FG_DIM)
        self.cases_status.pack(fill="x", padx=20, pady=(0, 4))

        tip = tk.Label(
            tab,
            text=('Tip: If you see a collection named "langchain", that is data from '
                  'the old ingestor. Use Rename to give it a proper case name.'),
            font=FONT_SMALL, bg=BG, fg=FG_MUTED, wraplength=700, justify="left")
        tip.pack(fill="x", padx=20, pady=(0, 16))

    def _refresh_case_lists(self):
        try:
            cases = list_cases()
            logger.info("Refreshed case list: %d case(s) -- %s",
                        len(cases),
                        ", ".join(f"{c['name']}({c['chunks']})" for c in cases) or "(empty)")
        except Exception as e:
            logger.error("Failed to refresh case list: %s", e, exc_info=True)
            cases = []

        for item in self.cases_tree.get_children():
            self.cases_tree.delete(item)
        for c in cases:
            self.cases_tree.insert("", "end", values=(c["name"], f"{c['chunks']:,}"))

        total_chunks = sum(c["chunks"] for c in cases)
        self.cases_status.configure(
            text=f"{len(cases)} case(s)  --  {total_chunks:,} total chunks")
        self._update_status(f"{len(cases)} case(s), {total_chunks:,} chunks indexed")

        names = [c["name"] for c in cases]
        self.query_case_combo["values"] = names
        if names and not self.query_case_var.get():
            saved = _load_settings().get("last_case", "")
            self.query_case_var.set(saved if saved in names else names[0])
        self.ingest_case_combo["values"] = names

    def _get_selected_case(self):
        sel = self.cases_tree.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Select a case first.")
            return None
        return self.cases_tree.item(sel[0])["values"][0]

    def _rename_case(self):
        old_name = self._get_selected_case()
        if not old_name:
            return
        new_name = simpledialog.askstring("Rename Case", f"Rename '{old_name}' to:",
                                           initialvalue=old_name)
        if not new_name or new_name.strip() == old_name:
            return
        new_name = new_name.strip().lower().replace(" ", "_")

        existing = [c["name"] for c in list_cases()]
        if new_name in existing:
            messagebox.showerror("Error", f"'{new_name}' already exists.")
            return
        if not messagebox.askyesno("Confirm Rename",
                f"Rename '{old_name}' -> '{new_name}'?"):
            return

        def run():
            try:
                # Case is a column, not a table, so this is an in-place
                # update rather than a copy-then-delete.
                total = store.rename_case(old_name, new_name)
                self.root.after(0, self._refresh_case_lists)
                self.root.after(0, messagebox.showinfo, "Renamed",
                                f"'{old_name}' -> '{new_name}' ({total:,} chunks)")
            except Exception as e:
                self.root.after(0, messagebox.showerror, "Error", str(e))
        self.cases_status.configure(text=f"Renaming '{old_name}' -> '{new_name}'...")
        threading.Thread(target=run, daemon=True).start()

    def _clone_case(self):
        src_name = self._get_selected_case()
        if not src_name:
            return
        new_name = simpledialog.askstring("Clone Case", f"Clone '{src_name}' to:")
        if not new_name:
            return
        new_name = new_name.strip().lower().replace(" ", "_")
        existing = [c["name"] for c in list_cases()]
        if new_name in existing:
            messagebox.showerror("Error", f"'{new_name}' already exists.")
            return

        def run():
            try:
                # Vectors are copied as-is, never re-embedded, so the clone
                # cannot drift from its source.
                total = store.clone_case(src_name, new_name)
                self.root.after(0, self._refresh_case_lists)
                self.root.after(0, messagebox.showinfo, "Cloned",
                                f"'{src_name}' cloned to '{new_name}' ({total:,} chunks)")
            except Exception as e:
                self.root.after(0, messagebox.showerror, "Error", str(e))
        self.cases_status.configure(text=f"Cloning '{src_name}'...")
        threading.Thread(target=run, daemon=True).start()

    def _delete_case(self):
        name = self._get_selected_case()
        if not name:
            return
        if not messagebox.askyesno("Confirm Delete",
                f"Delete all indexed data for '{name}'?\nThis cannot be undone."):
            return
        try:
            # Removes manifest rows too. Leaving them would make the next
            # ingest think those files are still indexed and skip them.
            removed = store.delete_case(name)
            self._refresh_case_lists()
            messagebox.showinfo("Deleted",
                                f"'{name}' deleted ({removed:,} chunks).")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete: {e}")

    # =========================================================================
    # SETTINGS TAB
    # =========================================================================
    def _build_settings_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Settings  ")

        canvas = tk.Canvas(tab, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(30, 0), pady=20)
        scrollbar.pack(side="right", fill="y", pady=20)

        row = 0
        saved = _load_settings()

        # -- LLM Section ------
        tk.Label(inner, text="AI / LLM Providers", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 12)); row += 1

        tk.Label(inner, text="Default Provider:", font=FONT, bg=BG, fg=FG
                 ).grid(row=row, column=0, sticky="w", pady=6)
        self._settings_provider_var = tk.StringVar(value=saved.get("llm_provider", "ollama"))
        prov_combo = ttk.Combobox(inner, textvariable=self._settings_provider_var,
                                   values=["ollama", "claude", "openai"], width=12, state="readonly")
        prov_combo.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=6)
        def save_provider(*_):
            save_settings({"llm_provider": self._settings_provider_var.get()})
        self._settings_provider_var.trace_add("write", save_provider)
        row += 1

        # -- Ollama ---
        tk.Label(inner, text="", bg=BG).grid(row=row, column=0, pady=4); row += 1
        tk.Label(inner, text="Ollama (local)", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 6)); row += 1

        tk.Label(inner, text="Model:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self._ollama_model = saved.get("ollama_model", DEFAULT_OLLAMA_MODEL)
        self.ollama_model_var = tk.StringVar(value=self._ollama_model)
        model_combo = ttk.Combobox(inner, textvariable=self.ollama_model_var, width=25)
        model_combo.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=6)

        def refresh_models():
            models = list_ollama_models()
            model_combo["values"] = models if models else ["gemma", "llama3", "mistral"]
        ttk.Button(inner, text="Detect Models", command=refresh_models).grid(
            row=row, column=2, padx=(10, 0), pady=6); row += 1

        def save_model(*_):
            self._ollama_model = self.ollama_model_var.get()
            save_settings({"ollama_model": self._ollama_model})
        self.ollama_model_var.trace_add("write", save_model)

        # -- Claude API ---
        tk.Label(inner, text="", bg=BG).grid(row=row, column=0, pady=4); row += 1
        tk.Label(inner, text="Claude (Anthropic API)", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 6)); row += 1

        tk.Label(inner, text="API Key:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self._claude_api_key_var = tk.StringVar(value=saved.get("claude_api_key", ""))
        claude_key_entry = ttk.Entry(inner, textvariable=self._claude_api_key_var,
                                      width=45, show="*")
        claude_key_entry.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=6)

        def toggle_claude_key():
            current = claude_key_entry.cget("show")
            claude_key_entry.configure(show="" if current == "*" else "*")
        ttk.Button(inner, text="Show/Hide", command=toggle_claude_key).grid(
            row=row, column=2, padx=(10, 0), pady=6); row += 1

        tk.Label(inner, text="Model:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self._claude_model_var = tk.StringVar(
            value=saved.get("claude_model", DEFAULT_CLAUDE_MODEL))
        ttk.Combobox(inner, textvariable=self._claude_model_var,
                     values=["claude-sonnet-4-20250514", "claude-haiku-4-20250414",
                             "claude-opus-4-20250514"],
                     width=30).grid(row=row, column=1, sticky="w", padx=(10, 0), pady=6)
        row += 1

        def save_claude(*_):
            save_settings({
                "claude_api_key": self._claude_api_key_var.get(),
                "claude_model": self._claude_model_var.get(),
            })
        self._claude_api_key_var.trace_add("write", save_claude)
        self._claude_model_var.trace_add("write", save_claude)

        # -- OpenAI ---
        tk.Label(inner, text="", bg=BG).grid(row=row, column=0, pady=4); row += 1
        tk.Label(inner, text="OpenAI / LM Studio", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 6)); row += 1

        tk.Label(inner, text="API Key:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self._openai_api_key_var = tk.StringVar(value=saved.get("openai_api_key", ""))
        openai_key_entry = ttk.Entry(inner, textvariable=self._openai_api_key_var,
                                      width=45, show="*")
        openai_key_entry.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=6)

        def toggle_openai_key():
            current = openai_key_entry.cget("show")
            openai_key_entry.configure(show="" if current == "*" else "*")
        ttk.Button(inner, text="Show/Hide", command=toggle_openai_key).grid(
            row=row, column=2, padx=(10, 0), pady=6); row += 1

        tk.Label(inner, text="API URL:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self._openai_url_var = tk.StringVar(
            value=saved.get("openai_url", DEFAULT_OPENAI_URL))
        ttk.Entry(inner, textvariable=self._openai_url_var, width=45).grid(
            row=row, column=1, sticky="w", padx=(10, 0), pady=6)
        tk.Label(inner, text="(LM Studio: http://localhost:1234/v1)",
                 font=FONT_SMALL, bg=BG, fg=FG_MUTED).grid(
            row=row, column=2, sticky="w", padx=(10, 0), pady=6); row += 1

        tk.Label(inner, text="Model:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self._openai_model_var = tk.StringVar(
            value=saved.get("openai_model", DEFAULT_OPENAI_MODEL))
        ttk.Combobox(inner, textvariable=self._openai_model_var,
                     values=["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo",
                             "local-model"],
                     width=25).grid(row=row, column=1, sticky="w", padx=(10, 0), pady=6)
        row += 1

        def save_openai(*_):
            save_settings({
                "openai_api_key": self._openai_api_key_var.get(),
                "openai_url": self._openai_url_var.get(),
                "openai_model": self._openai_model_var.get(),
            })
        self._openai_api_key_var.trace_add("write", save_openai)
        self._openai_url_var.trace_add("write", save_openai)
        self._openai_model_var.trace_add("write", save_openai)

        # -- Chat Behavior Section ---
        tk.Label(inner, text="", bg=BG).grid(row=row, column=0, pady=8); row += 1
        tk.Label(inner, text="Chat Behavior", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 10)); row += 1

        # Streaming responses (default ON, no extra cost)
        self._streaming_var = tk.BooleanVar(
            value=bool(saved.get("streaming_enabled", True)))
        ttk.Checkbutton(
            inner, text="Stream responses (live token-by-token output)",
            variable=self._streaming_var,
            command=lambda: save_settings({"streaming_enabled": self._streaming_var.get()}),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1

        # Multi-turn memory (default OFF — costs more tokens for paid providers)
        self._multi_turn_var = tk.BooleanVar(
            value=bool(saved.get("multi_turn_history", False)))
        ttk.Checkbutton(
            inner,
            text="Multi-turn memory  (paid providers: each turn re-sends prior messages = more tokens)",
            variable=self._multi_turn_var,
            command=lambda: save_settings({"multi_turn_history": self._multi_turn_var.get()}),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1

        # Chat autosave (default ON — local only, no cost)
        self._autosave_var = tk.BooleanVar(
            value=bool(saved.get("chat_autosave", True)))
        ttk.Checkbutton(
            inner, text="Auto-save chat sessions to chat_sessions/ folder",
            variable=self._autosave_var,
            command=lambda: save_settings({"chat_autosave": self._autosave_var.get()}),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1

        # History depth for multi-turn (only applies when multi-turn is on)
        tk.Label(inner, text="History depth (turns):", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self._history_depth_var = tk.IntVar(value=int(saved.get("history_depth", 6)))
        ttk.Spinbox(
            inner, from_=2, to=40, textvariable=self._history_depth_var, width=6,
            command=lambda: save_settings({"history_depth": self._history_depth_var.get()}),
        ).grid(row=row, column=1, sticky="w", padx=(10, 0), pady=6)
        tk.Label(inner, text="(higher = more context but more tokens)",
                 font=FONT_SMALL, bg=BG, fg=FG_MUTED).grid(
            row=row, column=2, sticky="w", padx=(10, 0), pady=6); row += 1

        # -- OCR Section ---
        tk.Label(inner, text="", bg=BG).grid(row=row, column=0, pady=8); row += 1
        tk.Label(inner, text="OCR (Scanned Documents)", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 10)); row += 1

        tk.Label(inner, text="Tesseract Path:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self.tesseract_var = tk.StringVar(
            value=saved.get("tesseract_cmd", get_tesseract_cmd()))
        ttk.Entry(inner, textvariable=self.tesseract_var, width=45).grid(
            row=row, column=1, sticky="w", padx=(10, 0), pady=6)

        def browse_tesseract():
            path = filedialog.askopenfilename(title="Select tesseract.exe",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
            if path:
                self.tesseract_var.set(path)
        ttk.Button(inner, text="Browse...", command=browse_tesseract).grid(
            row=row, column=2, padx=(10, 0), pady=6); row += 1

        tk.Label(inner, text="Poppler Path:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=6)
        self.poppler_var = tk.StringVar(
            value=saved.get("poppler_path", get_poppler_path()))
        ttk.Entry(inner, textvariable=self.poppler_var, width=45).grid(
            row=row, column=1, sticky="w", padx=(10, 0), pady=6)

        def browse_poppler():
            path = filedialog.askdirectory(title="Select Poppler bin folder")
            if path:
                self.poppler_var.set(path)
        ttk.Button(inner, text="Browse...", command=browse_poppler).grid(
            row=row, column=2, padx=(10, 0), pady=6); row += 1

        self.ocr_status_label = tk.Label(inner, text="", font=FONT, bg=BG, fg=FG_DIM,
                                          wraplength=500, justify="left")
        self.ocr_status_label.grid(row=row, column=0, columnspan=3, sticky="w",
                                    pady=(4, 2)); row += 1

        btn_row_ocr = tk.Frame(inner, bg=BG)
        btn_row_ocr.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 6)); row += 1

        self._ocr_test_btn = ttk.Button(btn_row_ocr, text="Save & Test OCR",
                                        style="Accent.TButton")
        self._ocr_test_btn.pack(side="left")

        def save_and_test_ocr(save_paths=True):
            tess_val = self.tesseract_var.get().strip()
            pop_val = self.poppler_var.get().strip()
            self._ocr_test_btn.configure(state="disabled")
            self.ocr_status_label.configure(text="Testing OCR...", fg=FG_DIM)

            def _test():
                try:
                    if save_paths:
                        save_settings({
                            "tesseract_cmd": tess_val,
                            "poppler_path": pop_val,
                        })
                    import ingest_engine
                    ingest_engine._ocr_available = None
                    ingest_engine._ocr_status = ""
                    ok = check_ocr()
                    status = get_ocr_status()

                    def _update_ui():
                        self._ocr_test_btn.configure(state="normal")
                        self.ocr_status_label.configure(
                            text=status, fg=TEAL if ok else ROSE)
                    self.root.after(0, _update_ui)
                except Exception as e:
                    def _show_err():
                        self._ocr_test_btn.configure(state="normal")
                        self.ocr_status_label.configure(
                            text=f"OCR test failed: {e}", fg=ROSE)
                    self.root.after(0, _show_err)

            threading.Thread(target=_test, daemon=True).start()

        self._ocr_test_btn.configure(command=save_and_test_ocr)
        self._run_ocr_check = lambda: save_and_test_ocr(save_paths=False)

        tk.Label(inner, text=(
            "Tesseract: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "Poppler:   https://github.com/oschwartz10612/poppler-windows/releases"
        ), font=FONT_SMALL, bg=BG, fg=FG_MUTED, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(2, 6)); row += 1

        # -- System Info ---
        tk.Label(inner, text="", bg=BG).grid(row=row, column=0, pady=8); row += 1
        tk.Label(inner, text="System Info", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 10)); row += 1

        tk.Label(inner, text="Database:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=4)
        import store as _store
        self.db_path_label = tk.Label(inner, text=_store.VECTORS_DIR,
                                      font=FONT_SMALL, bg=BG, fg=FG_DIM,
                                      wraplength=420, justify="left", anchor="w")
        self.db_path_label.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=4)
        ttk.Button(inner, text="Change...",
                   command=self._change_db_location).grid(
            row=row, column=2, padx=(10, 0), pady=4); row += 1

        tk.Label(inner, text="Embeddings:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=4)
        tk.Label(inner, text="all-MiniLM-L6-v2 (384-dim)", font=FONT_SMALL,
                 bg=BG, fg=FG_DIM).grid(
            row=row, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=4); row += 1

        tk.Label(inner, text="Log File:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=4)
        tk.Label(inner, text=LOG_FILE, font=FONT_SMALL, bg=BG, fg=FG_DIM).grid(
            row=row, column=1, sticky="w", padx=(10, 0), pady=4)
        ttk.Button(inner, text="Open Log", command=self._open_log_file).grid(
            row=row, column=2, padx=(10, 0), pady=4); row += 1

        tk.Label(inner, text="Updates:", font=FONT, bg=BG, fg=FG).grid(
            row=row, column=0, sticky="w", pady=4)
        tk.Label(inner, text=f"Installed v{_app_version()}",
                 font=FONT_SMALL, bg=BG, fg=FG_DIM).grid(
            row=row, column=1, sticky="w", padx=(10, 0), pady=4)
        ttk.Button(inner, text="Check for Updates",
                   command=lambda: self._check_for_updates(silent=False)).grid(
            row=row, column=2, padx=(10, 0), pady=4); row += 1

        tk.Label(inner, text="", bg=BG).grid(row=row, column=0, pady=10); row += 1
        tk.Label(inner, text=f"LegalMind Pro v{_app_version()}", font=FONT_HEADING,
                 bg=BG, fg=ACCENT_LIGHT).grid(
            row=row, column=0, columnspan=3, sticky="w"); row += 1
        tk.Label(inner, text="Local-first legal document intelligence. No data leaves your machine.",
                 font=FONT_SMALL, bg=BG, fg=FG_MUTED).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.root.after(1500, self._run_ocr_check)


# =============================================================================
# Splash Screen
# =============================================================================
class SplashScreen:
    _STEPS = [
        ("Loading PDF engine...",       "_load_fitz"),
        ("Loading AI models...",        "_load_langchain"),
        ("Connecting to database...",   "_load_vector_store"),
    ]

    def __init__(self, logger):
        self.logger = logger
        self.root = tk.Tk()
        self.root.title("LegalMind Pro")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        w, h = 480, 240
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "legalmind.ico")
        try:
            self.root.iconbitmap(ico_path)
        except Exception:
            pass

        frame = tk.Frame(self.root, bg=BG, padx=36, pady=28)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="LegalMind", font=("Segoe UI", 22, "bold"),
                 bg=BG, fg=ACCENT_LIGHT).pack(anchor="w")
        tk.Label(frame, text="Pro", font=("Segoe UI", 22),
                 bg=BG, fg=FG_DIM).pack(anchor="w")
        tk.Label(frame, text="Legal Document Intelligence", font=FONT,
                 bg=BG, fg=FG_MUTED).pack(anchor="w", pady=(2, 18))

        self.status = tk.Label(frame, text="Initializing...", font=FONT,
                               bg=BG, fg=FG)
        self.status.pack(anchor="w")

        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=400)
        self.progress.pack(anchor="w", pady=(12, 0))
        self.progress.start(20)

        self.root.update()

        self._done = False
        self._error = None
        self._thread = threading.Thread(target=self._bg_load, daemon=True)
        self._thread.start()

    def _bg_load(self):
        try:
            for label, method_name in self._STEPS:
                self._set_status(label)
                getattr(self, method_name)()
        except Exception as e:
            self._error = str(e)
        finally:
            self._done = True

    def _set_status(self, text):
        try:
            self.root.after(0, lambda t=text: self.status.configure(text=t))
        except Exception:
            pass

    def _load_fitz(self):
        try:
            import fitz
            self.logger.debug("PyMuPDF loaded OK")
        except Exception as e:
            self.logger.warning("Failed to load PyMuPDF: %s", e)

    def _load_langchain(self):
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            self.logger.debug("LangChain loaded OK")
        except Exception as e:
            self.logger.warning("Failed to load LangChain: %s", e)

    def _load_vector_store(self):
        try:
            import store
            store.connect()
            self.logger.debug("LanceDB store connected OK")
        except Exception as e:
            self.logger.warning("Failed to connect vector store: %s", e)

    def wait(self):
        while not self._done:
            self.root.update()
            time.sleep(0.05)
        self.progress.stop()
        if self._error:
            self.logger.warning("Splash import error: %s", self._error)

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass


# =============================================================================
# Entry Point
# =============================================================================
def _setup_gui_logging():
    root_logger = logging.getLogger("legalmind")
    if not root_logger.handlers:
        root_logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        root_logger.addHandler(fh)
    return root_logger


def main():
    logger = _setup_gui_logging()
    logger.info("=== LegalMind Pro v%s starting ===", _app_version())

    splash = SplashScreen(logger)
    splash.wait()
    splash.destroy()

    root = tk.Tk()
    ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "legalmind.ico")
    try:
        root.iconbitmap(ico_path)
    except Exception:
        pass

    try:
        app = LegalMindApp(root)
        logger.info("GUI ready -- entering mainloop")
        root.mainloop()
    except Exception as e:
        logger.critical("FATAL: %s", e, exc_info=True)
        messagebox.showerror("LegalMind Fatal Error",
                             f"An unexpected error occurred:\n\n{e}\n\n"
                             f"See legalmind.log for details.")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        here = os.path.dirname(os.path.abspath(__file__))
        crash_log = os.path.join(here, "crash.log")
        tb_text = traceback.format_exc()
        try:
            with open(crash_log, "w", encoding="utf-8") as f:
                f.write(f"LegalMind CRASH at {datetime.now()}\n\n")
                f.write(tb_text)
        except Exception:
            pass
        # Try to show a messagebox even when no Tk root is alive (pythonw silent crash)
        shown = False
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb
            _r = _tk.Tk(); _r.withdraw()
            _mb.showerror(
                "LegalMind Crash",
                f"Fatal error:\n{e}\n\nDetails saved to:\n{crash_log}")
            _r.destroy()
            shown = True
        except Exception:
            pass
        if not shown:
            # Last resort: Windows MessageBox via ctypes (works without Tk)
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"Fatal error:\n{e}\n\nDetails saved to:\n{crash_log}",
                    "LegalMind Crash", 0x10)
            except Exception:
                pass
