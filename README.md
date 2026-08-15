# LegalMind Pro

**Local-first legal document intelligence. No data leaves your machine.**

LegalMind Pro is a Retrieval-Augmented Generation (RAG) system built for legal professionals. It ingests your case documents (PDFs), indexes them into a local vector database, and lets you search across thousands of pages using natural language queries. AI analysis is optional and provider-agnostic: a local Ollama model (fully offline), your Claude subscription, the Claude API, or any OpenAI-compatible endpoint.

---

## Features

- **Multi-case collections** — Organize documents by case; rename, clone, and delete cases in place
- **LanceDB vector store** — Fast, columnar, memory-mapped local index (no server, no SQLite row limits)
- **Crash-safe incremental ingest** — Per-file commits with an explicit manifest; interrupt any time and resume with no duplicates and no half-indexed files
- **Smart OCR** — OCR-scans pages that are blank *or* have a corrupt text layer (broken font encodings that would otherwise be indexed as gibberish); requires Tesseract + Poppler
- **Background mode** — A switch + slider to throttle ingestion to spare CPU only, so a multi-hour run stays out of your way
- **Extracted-text fast path** — Ingest pre-extracted, de-duplicated text directly, skipping PDF rendering and OCR entirely
- **Semantic search** — Find relevant passages using meaning, not just keywords
- **Document type classification** — Auto-classifies filings (Motion, Order, Affidavit, Exhibit, etc.)
- **Choice of AI provider** — Defaults to your **Claude subscription** (Claude Code CLI — no API key, no per-token cost); also a local **Ollama** model, the **Claude API**, or any **OpenAI-compatible** endpoint
- **Dark-themed GUI** — Professional desktop interface with real-time ingest progress
- **CLI tools** — Command-line ingestor and query tool for power users
- **MCP server** — Expose your legal database to Claude Desktop as a tool
- **Export results** — Copy or save query results to text files
- **In-app auto-update** — Checks GitHub for new releases on startup and offers a one-click download
- **Portable database** — Point the app at any database folder (Settings → Database → Change), the `LEGALMIND_VECTORS` env var, or a saved setting. A fresh install on a new machine connects to your existing index instead of starting empty.
- **Local by default** — Documents are stored and searched entirely on your machine. AI analysis is *your* choice: **Ollama keeps everything on-device**, while the Claude/OpenAI options send only the retrieved snippets and your question to that provider.

---

## Quick Start

### 1. Install

**From a release (recommended):** download the latest `LegalMind-Pro-vX.Y.Z.zip` from the
[Releases page](https://github.com/sasha-thecornerspore-dev/legalmind-pro/releases/latest),
unzip it, and run `install.bat`.

**From source:**

```bash
git clone https://github.com/sasha-thecornerspore-dev/legalmind-pro.git
cd legalmind-pro
```

- **Windows:** run `install.bat` (installs dependencies + desktop shortcut), then launch with `legalmind.bat`.
- **macOS / Linux:** run `./install.sh` — it installs the Python dependencies and, if Tesseract/Poppler/Tk are missing, **offers to install them for you** via Homebrew (macOS) or apt/dnf/pacman (Linux). Then launch with `./legalmind.command` (double-click in Finder on macOS) or `python3 gui.py`. Pass `-y` to accept all prompts.

Or install dependencies manually with `pip install -r requirements.txt`.

> **macOS / Linux note:** `install.sh` detects whatever is missing (Tk for the GUI,
> Tesseract + Poppler for OCR) and offers to install it via your package manager
> (Homebrew / apt / dnf / pacman) — it will even offer to install Homebrew on macOS.
> Run `./install.sh -y` to accept everything non-interactively.

The embedding model (`all-MiniLM-L6-v2`) downloads automatically on first run, or
rebuild it from a local HuggingFace cache with `python tools/rebuild_model.py`.

### 2. Launch

Double-click **LegalMind Pro** on your desktop, or:

```bash
python gui.py
```

### 3. Ingest Documents

1. Go to the **Ingest** tab
2. Enter a case name (e.g., `foreclosure`)
3. Browse to a folder containing PDFs
4. Click **Start Ingestion**
5. Watch real-time progress as files are extracted, chunked, and embedded

### 4. Search

1. Go to the **Query** tab
2. Select your case from the dropdown
3. Type a natural language question
4. Press Enter or click Search

---

## System Requirements

- **Python 3.10+** (tested on 3.10–3.14)
- **Windows 10/11, macOS, or Linux** — cross-platform (Python + tkinter)
- **4 GB RAM minimum**, 8+ GB recommended for large collections
- **Disk space**: ~1.5 GB per 100K document chunks indexed

### Optional Dependencies

| Component | Purpose | Download |
|-----------|---------|----------|
| **Tesseract** | OCR for scanned PDFs | Win: [installer](https://github.com/UB-Mannheim/tesseract/wiki) · macOS: `brew install tesseract` · Linux: `apt install tesseract-ocr` |
| **Poppler** | PDF-to-image conversion for OCR | Win: [releases](https://github.com/oschwartz10612/poppler-windows/releases) · macOS: `brew install poppler` · Linux: `apt install poppler-utils` |
| **Ollama** | Local AI analysis of search results | [ollama.com](https://ollama.com) |

---

## Project Structure

```
legal-rag/
  gui.py              # Main GUI application (tkinter)
  core.py              # Shared config, version, embeddings, Ollama
  store.py             # LanceDB vector store (the only module that touches the DB)
  throttle.py          # CPU-aware background throttle
  updater.py           # In-app GitHub release update checker
  ingest_engine.py     # PDF extraction, chunking, vectorization engine
  ingest.py            # Standalone CLI ingestor (Rich progress bars)
  query.py             # CLI query tool
  diagnose.py          # Database diagnostic & migration utility
  legal_rag_mcp.py     # MCP server for Claude Desktop integration
  legalmind.bat        # GUI launcher (Windows)
  legalmind_debug.bat  # Debug launcher (shows console output)
  create_shortcut.bat  # Creates a desktop shortcut (Windows)
  install.bat          # One-click installer (Windows)
  legalmind.command    # GUI launcher (macOS / Linux, double-clickable)
  install.sh           # Installer (macOS / Linux)
  legalmind.ico        # Application icon
  requirements.txt     # Python dependencies
  vectors/             # LanceDB vector store (created on first ingest, gitignored)
  models/              # Local embedding model (gitignored)
  tests/               # pytest suite
  .github/workflows/   # Release automation (builds an installable zip on each tag)
```

---

## Architecture

```
PDF Files
    │
    ▼
┌─────────────────────┐
│  PyMuPDF + OCR      │  Extract text (+ Tesseract fallback for scans)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│  LangChain Splitter │  Chunk into 1200-char segments with 200-char overlap
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│  all-MiniLM-L6-v2   │  Generate 384-dimensional embeddings (L2-normalized)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│  LanceDB            │  Store vectors + metadata locally (columnar, mmap'd)
│                     │  One table; case is a column. Per-file commit manifest.
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│  Cosine Search       │  Top-k by cosine distance; relevance = 1 - distance,
│                     │  gated at 0.35 to keep noise out of AI answers
└─────────┬───────────┘
          ▼
┌──────────────────────────┐
│  LLM provider (optional) │  AI analysis with source citations —
│  Ollama · Claude sub ·   │  Ollama runs on-device; Claude/OpenAI
│  Claude API · OpenAI     │  receive only the retrieved snippets
└──────────────────────────┘
```

---

## CLI Usage

### Ingest (with Rich progress bars)

```bash
python ingest.py
```

Opens a dialog to select a case name and folder, then shows a detailed progress bar.

### Query (interactive)

```bash
python query.py
```

Interactive CLI that lets you pick a case, type queries, and see results with relevance scores.

### Diagnose Database

```bash
python diagnose.py
```

Shows all collections, chunk counts, and lets you rename or migrate collections.

---

## MCP Server (Claude Desktop)

LegalMind can expose your legal database as tools for Claude Desktop:

1. Install the MCP package: `pip install mcp`
2. Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "legal-rag": {
      "command": "python",
      "args": ["C:\\path\\to\\legal-rag\\legal_rag_mcp.py"]
    }
  }
}
```

3. Restart Claude Desktop. You'll have tools like `search_legal_database` and `list_legal_cases` available.

---

## Configuration

Settings are saved to `settings.json` and can be changed in the GUI Settings tab:

- **AI provider**: **defaults to `claude-cli`** — your Claude Pro/Max **subscription** via the [Claude Code](https://claude.com/claude-code) CLI (run `claude` once to log in; no API key, no per-token cost), falling back to local `ollama` if the CLI isn't installed. Also selectable: `claude` (Anthropic API key) and `openai` (any OpenAI-compatible endpoint).
- **Ollama model**: Which local LLM to use for analysis (default: `gemma`)
- **Tesseract path**: Path to `tesseract.exe` for OCR
- **Poppler path**: Path to Poppler's `bin` directory for OCR
- **Chunk size**: Characters per text chunk (default: 1200)
- **Background mode**: Throttle ingestion to spare CPU only (switch)
- **Leave N% CPU free**: How much headroom to preserve while ingesting (slider, default 50%)

---

## Troubleshooting

**App takes a long time to start**: LangChain imports are heavy. The splash screen shows loading progress. First launch may take 1–3 minutes.

**"Too many SQL variables" error**: This was fixed in v5.3+. Make sure you're using the latest version. All database operations now use paginated queries.

**OCR not working**: Check Settings tab — make sure Tesseract and Poppler paths are correct. Click "Save & Test OCR" to verify.

**GUI freezes during ingest**: Fixed in v7.0+. All heavy operations now yield the GIL between batches to keep the UI responsive.

**No results found**: Make sure you've selected the correct case and that ingest completed successfully. Check the Cases tab for chunk counts.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Credits

Built with:
- [LanceDB](https://lancedb.com/) — columnar, memory-mapped vector database
- [LangChain](https://langchain.com/) — recursive text splitting
- [sentence-transformers](https://www.sbert.net/) — embedding model (all-MiniLM-L6-v2)
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF text extraction
- [Tesseract](https://github.com/tesseract-ocr/tesseract) — OCR engine
- [Ollama](https://ollama.com/) — local LLM runtime
- [Rich](https://rich.readthedocs.io/) — CLI progress bars
