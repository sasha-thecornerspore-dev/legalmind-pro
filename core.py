"""
LegalMind Core — shared configuration, embeddings, and database utilities.
Used by ingest, query, GUI, and MCP server.
"""

import io
import logging
import os
import sys
import warnings
from typing import Optional

# ── pythonw stdout/stderr fix ─────────────────────────────────────────────────
# pythonw.exe sets sys.stdout/stderr to None, crashing third-party libs that
# call sys.stdout.isatty() (transformers, tqdm, etc).  Must run before imports.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# ── Global subprocess console-window suppression (Windows) ────────────────────
# Without this, every subprocess (pytesseract, pdf2image/poppler, huggingface,
# chromadb compile steps, etc.) flashes a CMD window because pythonw inherits
# no console -- children create their own. Patch BEFORE other imports.
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

warnings.filterwarnings("ignore", message=".*Pydantic.*")

logger = logging.getLogger("legalmind.core")

# ── Version ──────────────────────────────────────────────────────────────────
# Single source of truth. The in-app updater compares this against the latest
# GitHub Release tag (see updater.py). Bump on every tagged release.
VERSION = "11.4.0"

# Repo the in-app updater checks for new releases. Forks can repoint it with
# the LEGALMIND_REPO environment variable without editing source.
GITHUB_REPO = os.environ.get("LEGALMIND_REPO", "sasha-thecornerspore-dev/legalmind-pro")

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Embedding model resolution: prefer a locally rebuilt copy under models/ (so
# the app can run fully offline once it exists), otherwise fall back to the
# HuggingFace hub id so a fresh install downloads it automatically on first use.
# A bare local path that does not exist would crash HuggingFaceEmbeddings, so
# the fallback is what makes a clean clone work for anyone.
EMBED_MODEL_DIR = os.path.join(BASE_DIR, "models", "all-MiniLM-L6-v2")
EMBED_MODEL_HUB = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_MODEL = EMBED_MODEL_DIR if os.path.isdir(EMBED_MODEL_DIR) else EMBED_MODEL_HUB

# all-MiniLM-L6-v2 output width. Verified from the model's own
# 1_Pooling/config.json ("word_embedding_dimension": 384) and at runtime.
# store.py builds its Arrow schema from this, so a mismatch is a hard error
# at write time rather than silent corruption.
EMBED_DIM = 384

# ── OCR tool paths (platform-aware defaults — override via settings/env) ─────
# Tesseract:  Windows https://github.com/UB-Mannheim/tesseract/wiki
#             macOS   `brew install tesseract`   Linux `apt install tesseract-ocr`
# Poppler:    Windows https://github.com/oschwartz10612/poppler-windows/releases
#             macOS   `brew install poppler`     Linux `apt install poppler-utils`
def _first_existing(candidates, fallback):
    for c in candidates:
        if os.path.isfile(c):
            return c
    return fallback


def _default_tesseract() -> str:
    if sys.platform == "win32":
        return r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    # macOS (Homebrew on Apple Silicon / Intel) and Linux; else assume on PATH.
    return _first_existing(
        ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract",
         "/usr/bin/tesseract"],
        "tesseract")


def _default_poppler() -> str:
    """Directory containing pdftoppm. Empty string means 'use PATH'."""
    if sys.platform == "win32":
        return r"C:\Program Files\poppler\Library\bin"
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        if os.path.isfile(os.path.join(d, "pdftoppm")):
            return d
    return ""  # pdf2image will look on PATH


TESSERACT_CMD = os.environ.get("TESSERACT_CMD", _default_tesseract())
POPPLER_PATH = os.environ.get("POPPLER_PATH", _default_poppler())


def open_path(path: str) -> None:
    """Open a file or folder with the OS default handler. Cross-platform.

    os.startfile is Windows-only; macOS uses `open`, Linux uses `xdg-open`.
    """
    import subprocess
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: F401
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        logger.warning("could not open %s: %s", path, e)

# Settings file for persisted user overrides (JSON)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")


def _load_settings() -> dict:
    """Load persisted settings from settings.json (if it exists)."""
    import json
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(data: dict):
    """Persist settings to settings.json."""
    import json
    existing = _load_settings()
    existing.update(data)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def get_tesseract_cmd() -> str:
    return _load_settings().get("tesseract_cmd", TESSERACT_CMD)


def get_poppler_path() -> str:
    return _load_settings().get("poppler_path", POPPLER_PATH)

# ── Lazy-loaded singletons ───────────────────────────────────────────────────
_embeddings = None


def get_embeddings():
    """Return a cached HuggingFaceEmbeddings instance."""
    global _embeddings
    if _embeddings is None:
        logger.debug("Loading HuggingFace embeddings model: %s", EMBED_MODEL)
        from langchain_huggingface import HuggingFaceEmbeddings
        _embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
        logger.debug("Embeddings model loaded successfully.")
    return _embeddings


# ── Document type classification ─────────────────────────────────────────────
DOC_TYPE_KEYWORDS = {
    "Motion":    ["motion", "mot_", "mtn"],
    "Order":     ["order", "ruling", "judgment", "decree"],
    "Affidavit": ["affidavit", "sworn", "declaration"],
    "Exhibit":   ["exhibit", "exh_", "exh-"],
    "Notice":    ["notice", "ntc"],
    "Complaint": ["complaint", "petition"],
    "Response":  ["response", "reply", "opposition", "objection"],
    "Discovery": ["interrogator", "discovery", "request for", "subpoena", "deposition"],
    "Brief":     ["brief", "memorandum", "memo"],
    "Contract":  ["contract", "agreement", "lease", "deed", "mortgage", "note"],
}


def classify_doc_type(filename: str) -> str:
    """Classify document type from filename using keyword matching."""
    f = filename.lower()
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        if any(kw in f for kw in keywords):
            return doc_type
    return "Other"


# ── LLM Providers ────────────────────────────────────────────────────────────
# Supported providers:
#   "ollama"     — local models (no cost, no cloud)
#   "claude-cli" — Claude via your Claude Code SUBSCRIPTION (no API key, no
#                  per-token billing); requires the `claude` CLI, logged in
#   "claude"     — Anthropic API (pay-per-token, needs an API key)
#   "openai"     — any OpenAI-compatible endpoint (needs an API key)
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
DEFAULT_CLAUDE_CLI_MODEL = ""   # empty => whatever Claude Code is configured to use
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"

LLM_PROVIDERS = ["ollama", "claude-cli", "claude", "openai"]

# Human-readable names for the provider dropdown.
PROVIDER_LABELS = {
    "ollama":     "Ollama (local)",
    "claude-cli": "Claude (subscription)",
    "claude":     "Claude (API key)",
    "openai":     "OpenAI-compatible (API key)",
}


def query_ollama(prompt: str, model: str = DEFAULT_OLLAMA_MODEL,
                 url: str = DEFAULT_OLLAMA_URL) -> str:
    """Send a prompt to a local Ollama instance and return the response."""
    import requests
    try:
        resp = requests.post(
            f"{url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except requests.exceptions.RequestException as e:
        return f"Error connecting to Ollama: {e}"


def list_ollama_models(url: str = DEFAULT_OLLAMA_URL) -> list[str]:
    """List models available on the local Ollama instance."""
    import requests
    try:
        resp = requests.get(f"{url}/api/tags", timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def query_claude(prompt: str, model: str = DEFAULT_CLAUDE_MODEL,
                 api_key: str = "") -> str:
    """Send a prompt to the Anthropic Claude API and return the response.

    Requires an Anthropic API key (from settings or env var ANTHROPIC_API_KEY).
    """
    import requests
    if not api_key:
        api_key = _load_settings().get("claude_api_key", "")
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ("Error: No Claude API key found. Set it in Settings -> Claude API Key, "
                "or set the ANTHROPIC_API_KEY environment variable.")
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        # Extract text from Claude's response content blocks
        content = data.get("content", [])
        return "\n".join(
            block.get("text", "") for block in content if block.get("type") == "text"
        )
    except requests.exceptions.RequestException as e:
        return f"Error connecting to Claude API: {e}"


def query_openai(prompt: str, model: str = DEFAULT_OPENAI_MODEL,
                 api_key: str = "", url: str = DEFAULT_OPENAI_URL) -> str:
    """Send a prompt to any OpenAI-compatible API (OpenAI, LM Studio, etc)."""
    import requests
    if not api_key:
        api_key = _load_settings().get("openai_api_key", "")
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key and "api.openai.com" in url:
        return ("Error: No OpenAI API key found. Set it in Settings -> OpenAI API Key, "
                "or set the OPENAI_API_KEY environment variable.")
    if not url:
        url = _load_settings().get("openai_url", DEFAULT_OPENAI_URL)
    try:
        headers = {"content-type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.post(
            f"{url.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return "No response received."
    except requests.exceptions.RequestException as e:
        return f"Error connecting to OpenAI-compatible API: {e}"


def _claude_cli_path() -> str:
    """Locate the Claude Code CLI executable, or '' if not installed."""
    import shutil
    exe = shutil.which("claude")
    if exe:
        return exe
    # Fall back to the default per-user install location.
    name = "claude.exe" if sys.platform == "win32" else "claude"
    cand = os.path.join(os.path.expanduser("~"), ".local", "bin", name)
    return cand if os.path.isfile(cand) else ""


def check_claude_cli() -> bool:
    """True if Claude can be used via the local subscription-backed CLI."""
    return bool(_claude_cli_path())


def query_claude_cli(prompt: str, model: str = DEFAULT_CLAUDE_CLI_MODEL,
                     timeout: int = 300) -> str:
    """Query Claude through the Claude Code CLI in print mode.

    Uses whatever the local `claude` CLI is logged in with — typically a
    Claude Pro/Max SUBSCRIPTION — so there is no API key and no per-token
    billing. The prompt is piped via stdin, which avoids Windows
    command-length limits on large RAG contexts.
    """
    import subprocess
    exe = _claude_cli_path()
    if not exe:
        return ("Error: Claude Code CLI not found. Install it from "
                "https://claude.com/claude-code, then run `claude` once to log "
                "in with your subscription.")
    args = [exe, "-p"]
    if model:
        args += ["--model", model]
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "Error: Claude CLI timed out."
    except Exception as e:
        return f"Error launching Claude CLI: {e}"
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        return (f"Error from Claude CLI (exit {r.returncode}): "
                f"{err or 'no output — are you logged in? run `claude` once.'}")
    return (r.stdout or "").strip()


def query_llm(prompt: str, provider: str = "ollama", model: str = "",
              **kwargs) -> str:
    """Unified LLM query dispatcher — routes to the configured provider.

    provider: "ollama" | "claude-cli" | "claude" | "openai"
    model: model name override (uses provider defaults if empty)
    """
    provider = provider.lower().strip()
    if provider in ("claude-cli", "claude-subscription"):
        return query_claude_cli(prompt, model=model or DEFAULT_CLAUDE_CLI_MODEL)
    if provider == "claude":
        return query_claude(prompt, model=model or DEFAULT_CLAUDE_MODEL, **kwargs)
    elif provider == "openai":
        return query_openai(prompt, model=model or DEFAULT_OPENAI_MODEL, **kwargs)
    else:  # default to ollama
        return query_ollama(prompt, model=model or DEFAULT_OLLAMA_MODEL, **kwargs)


# ── Streaming LLM helpers ────────────────────────────────────────────────────
# Each *_stream function calls on_token(chunk) for every text fragment received,
# returns the concatenated final text, and never raises -- network errors are
# delivered through on_token and returned as the final text so the caller has
# a uniform contract.

def query_ollama_stream(prompt: str, on_token, model: str = DEFAULT_OLLAMA_MODEL,
                        url: str = DEFAULT_OLLAMA_URL) -> str:
    import json
    import requests
    full = []
    try:
        with requests.post(
            f"{url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": True},
            stream=True, timeout=300,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                chunk = obj.get("response", "")
                if chunk:
                    full.append(chunk)
                    try: on_token(chunk)
                    except Exception: pass
                if obj.get("done"):
                    break
    except requests.exceptions.RequestException as e:
        msg = f"\n\n[Error connecting to Ollama: {e}]"
        try: on_token(msg)
        except Exception: pass
        full.append(msg)
    return "".join(full)


def query_claude_stream(messages: list, on_token, model: str = DEFAULT_CLAUDE_MODEL,
                        api_key: str = "", system: str = "") -> str:
    """Stream from Anthropic. messages: [{'role':'user'|'assistant','content':str}]."""
    import json
    import requests
    if not api_key:
        api_key = _load_settings().get("claude_api_key", "") \
            or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        msg = "Error: No Claude API key found. Set it in Settings."
        try: on_token(msg)
        except Exception: pass
        return msg

    payload = {"model": model, "max_tokens": 4096,
               "messages": messages, "stream": True}
    if system:
        payload["system"] = system

    full = []
    try:
        with requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload, stream=True, timeout=300,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data: "):
                    continue
                data = raw[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "content_block_delta":
                    text = (obj.get("delta") or {}).get("text", "")
                    if text:
                        full.append(text)
                        try: on_token(text)
                        except Exception: pass
                elif t == "message_stop":
                    break
                elif t == "error":
                    err = (obj.get("error") or {}).get("message", "Claude error")
                    msg = f"\n\n[Claude error: {err}]"
                    try: on_token(msg)
                    except Exception: pass
                    full.append(msg)
                    break
    except requests.exceptions.RequestException as e:
        msg = f"\n\n[Error connecting to Claude API: {e}]"
        try: on_token(msg)
        except Exception: pass
        full.append(msg)
    return "".join(full)


def query_openai_stream(messages: list, on_token, model: str = DEFAULT_OPENAI_MODEL,
                        api_key: str = "", url: str = DEFAULT_OPENAI_URL) -> str:
    import json
    import requests
    if not api_key:
        api_key = _load_settings().get("openai_api_key", "") \
            or os.environ.get("OPENAI_API_KEY", "")
    if not url:
        url = _load_settings().get("openai_url", DEFAULT_OPENAI_URL)

    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    full = []
    try:
        with requests.post(
            f"{url.rstrip('/')}/chat/completions",
            headers=headers,
            json={"model": model, "messages": messages,
                  "max_tokens": 4096, "stream": True},
            stream=True, timeout=300,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data: "):
                    continue
                data = raw[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                text = (choices[0].get("delta") or {}).get("content", "")
                if text:
                    full.append(text)
                    try: on_token(text)
                    except Exception: pass
    except requests.exceptions.RequestException as e:
        msg = f"\n\n[Error connecting to OpenAI-compatible API: {e}]"
        try: on_token(msg)
        except Exception: pass
        full.append(msg)
    return "".join(full)


def query_claude_cli_stream(messages: list, on_token, model: str = DEFAULT_CLAUDE_CLI_MODEL,
                            system: str = "", timeout: int = 600) -> str:
    """Streaming variant of the subscription-backed Claude CLI provider.

    Claude Code print mode is single-shot, so the conversation is flattened
    into one prompt (as with Ollama). stdout is read incrementally and pushed
    through on_token as it arrives.
    """
    import subprocess
    exe = _claude_cli_path()
    if not exe:
        msg = ("Error: Claude Code CLI not found. Install it from "
               "https://claude.com/claude-code and run `claude` once to log in.")
        on_token(msg)
        return msg

    if len(messages) == 1 and not system:
        prompt = messages[0].get("content", "")
    else:
        parts = []
        if system:
            parts.append(system)
        for m in messages:
            tag = "User" if m.get("role") == "user" else "Assistant"
            parts.append(f"{tag}: {m.get('content', '')}")
        prompt = "\n\n".join(parts)

    args = [exe, "-p"]
    if model:
        args += ["--model", model]

    acc = []
    try:
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1)
        proc.stdin.write(prompt)
        proc.stdin.close()
        for chunk in proc.stdout:
            acc.append(chunk)
            on_token(chunk)
        proc.wait(timeout=timeout)
        if proc.returncode != 0:
            err = (proc.stderr.read() or "").strip()
            tail = f"\n[Claude CLI error: {err or proc.returncode}]"
            on_token(tail)
            acc.append(tail)
    except Exception as e:
        tail = f"\n[Claude CLI error: {e}]"
        on_token(tail)
        acc.append(tail)
    return "".join(acc).strip()


def query_llm_stream(provider: str, on_token, model: str = "",
                     prompt: str = "", messages: list | None = None,
                     system: str = "", **kwargs) -> str:
    """Unified streaming dispatcher.

    Provide either ``prompt`` (single-turn) or ``messages`` (multi-turn).
    For Ollama and the Claude CLI, multi-turn ``messages`` get flattened.
    """
    provider = (provider or "ollama").lower().strip()
    if messages is None:
        messages = [{"role": "user", "content": prompt or ""}]

    if provider in ("claude-cli", "claude-subscription"):
        return query_claude_cli_stream(
            messages, on_token, model=model or DEFAULT_CLAUDE_CLI_MODEL,
            system=system or "")
    if provider == "claude":
        return query_claude_stream(
            messages, on_token,
            model=model or DEFAULT_CLAUDE_MODEL,
            api_key=kwargs.get("api_key", ""),
            system=system or "",
        )
    if provider == "openai":
        return query_openai_stream(
            messages, on_token,
            model=model or DEFAULT_OPENAI_MODEL,
            api_key=kwargs.get("api_key", ""),
            url=kwargs.get("url", DEFAULT_OPENAI_URL),
        )

    # Ollama: flatten conversation into a single prompt
    if prompt:
        flat = prompt
    else:
        parts = []
        if system:
            parts.append(f"System: {system}")
        for m in messages:
            tag = "User" if m.get("role") == "user" else "Assistant"
            parts.append(f"{tag}: {m.get('content','')}")
        parts.append("Assistant:")
        flat = "\n\n".join(parts)
    return query_ollama_stream(
        flat, on_token,
        model=model or DEFAULT_OLLAMA_MODEL,
        url=kwargs.get("url", DEFAULT_OLLAMA_URL),
    )


# ── Multi-turn (non-streaming) variants ──────────────────────────────────────
# Used when streaming is disabled but multi-turn memory is on.

def query_claude_messages(messages: list, model: str = DEFAULT_CLAUDE_MODEL,
                          api_key: str = "", system: str = "") -> str:
    import requests
    if not api_key:
        api_key = _load_settings().get("claude_api_key", "") \
            or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Error: No Claude API key found. Set it in Settings."
    payload = {"model": model, "max_tokens": 4096, "messages": messages}
    if system:
        payload["system"] = system
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload, timeout=180,
        )
        r.raise_for_status()
        content = r.json().get("content", [])
        return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    except requests.exceptions.RequestException as e:
        return f"Error connecting to Claude API: {e}"


def query_openai_messages(messages: list, model: str = DEFAULT_OPENAI_MODEL,
                          api_key: str = "", url: str = DEFAULT_OPENAI_URL) -> str:
    import requests
    if not api_key:
        api_key = _load_settings().get("openai_api_key", "") \
            or os.environ.get("OPENAI_API_KEY", "")
    if not url:
        url = _load_settings().get("openai_url", DEFAULT_OPENAI_URL)
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = requests.post(
            f"{url.rstrip('/')}/chat/completions",
            headers=headers,
            json={"model": model, "messages": messages, "max_tokens": 4096},
            timeout=180,
        )
        r.raise_for_status()
        choices = r.json().get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return "No response received."
    except requests.exceptions.RequestException as e:
        return f"Error connecting to OpenAI-compatible API: {e}"


# ── Analysis Modes (structured prompts for legal RAG) ─────────────────────────

ANALYSIS_MODES = {
    "standard": {
        "name": "Standard Analysis",
        "description": "Answer the question using the provided evidence",
        "prompt": """You are a senior litigation assistant. Answer the question using ONLY the provided evidence segments.
If the answer isn't in the evidence, say "Evidence not found."
Cite the specific source file and page for each claim.

EVIDENCE:
{context}

QUESTION:
{query}

ANALYSIS:""",
    },
    "timeline": {
        "name": "Timeline Builder",
        "description": "Build a chronological timeline from the evidence",
        "prompt": """You are a legal timeline analyst. Review the following evidence segments and construct a CHRONOLOGICAL TIMELINE of all events, dates, and actions mentioned.

For each entry, provide:
- DATE (exact or approximate)
- EVENT description
- SOURCE (filename and page number)
- SIGNIFICANCE to the case

If dates are ambiguous, note the uncertainty. Flag any contradictions in dates between sources.

EVIDENCE:
{context}

QUESTION/FOCUS:
{query}

CHRONOLOGICAL TIMELINE:""",
    },
    "contradictions": {
        "name": "Contradiction Finder",
        "description": "Identify contradictions and inconsistencies across documents",
        "prompt": """You are a legal analyst specializing in cross-examination preparation. Carefully review the following evidence segments and identify ALL contradictions, inconsistencies, or conflicting statements between documents.

For each contradiction found:
1. STATE the conflicting claims
2. CITE the specific sources (filename + page) for each side
3. ASSESS the severity (minor discrepancy vs. material contradiction)
4. SUGGEST follow-up questions that could resolve or exploit the contradiction

If no contradictions are found, state that clearly and note any areas where information is incomplete.

EVIDENCE:
{context}

FOCUS AREA:
{query}

CONTRADICTION ANALYSIS:""",
    },
    "evidence_chain": {
        "name": "Evidence Chain",
        "description": "Build a chain of evidence supporting or refuting a claim",
        "prompt": """You are a legal evidence analyst. The user wants to prove or disprove a specific claim. Review the evidence and build a structured EVIDENCE CHAIN.

Organize your analysis as:

SUPPORTING EVIDENCE:
- List each piece of evidence that supports the claim
- Cite source (filename + page)
- Rate strength: Strong / Moderate / Weak

OPPOSING EVIDENCE:
- List each piece that contradicts or weakens the claim
- Cite source (filename + page)
- Rate strength: Strong / Moderate / Weak

GAPS:
- What additional evidence would strengthen the case?
- What questions remain unanswered?

OVERALL ASSESSMENT:
- Is the claim well-supported, disputed, or unsupported?

EVIDENCE:
{context}

CLAIM TO EVALUATE:
{query}

EVIDENCE CHAIN ANALYSIS:""",
    },
    "case_summary": {
        "name": "Case Summary",
        "description": "Generate a comprehensive summary of the case from available evidence",
        "prompt": """You are a senior legal analyst preparing a case briefing. Using the provided evidence, create a comprehensive CASE SUMMARY.

Structure your summary as:

PARTIES INVOLVED:
- Identify all parties, their roles, and relationships

KEY FACTS:
- Summarize the essential facts of the case
- Cite sources for each major fact

LEGAL ISSUES:
- Identify the core legal questions or disputes

PROCEDURAL HISTORY:
- What legal actions have been taken so far?

KEY DOCUMENTS:
- Which documents are most critical and why?

STRENGTHS & WEAKNESSES:
- Assessment of the case's strong and weak points

EVIDENCE:
{context}

FOCUS/QUERY:
{query}

CASE SUMMARY:""",
    },
    "key_terms": {
        "name": "Key Terms Extractor",
        "description": "Extract and define important legal terms, names, amounts, and dates",
        "prompt": """You are a legal research assistant. Extract ALL key terms from the evidence, organized by category.

Categories:
- PARTIES & PEOPLE: names, roles, relationships
- DATES & DEADLINES: all dates mentioned and their significance
- MONETARY AMOUNTS: all dollar figures, fees, costs, damages
- LEGAL TERMS: statutes, case law, legal standards referenced
- PROPERTIES & ASSETS: any real property, accounts, or assets
- KEY AGREEMENTS: contracts, stipulations, orders referenced

For each item, cite the source (filename + page).

EVIDENCE:
{context}

FOCUS:
{query}

EXTRACTED KEY TERMS:""",
    },
}


def get_analysis_prompt(mode: str, context: str, query: str) -> str:
    """Build a prompt using the specified analysis mode template."""
    mode_config = ANALYSIS_MODES.get(mode, ANALYSIS_MODES["standard"])
    return mode_config["prompt"].format(context=context, query=query)

