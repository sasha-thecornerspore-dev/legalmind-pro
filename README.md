# LegalMind Pro

**Local-first legal document intelligence. No data leaves your machine.**

LegalMind Pro is a Retrieval-Augmented Generation (RAG) system built for legal professionals. It ingests your case documents (PDFs), indexes them into a local vector database, and lets you search across thousands of pages using natural language queries. Optionally integrates with [Ollama](https://ollama.com) for AI-powered analysis.

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
- **Ollama AI analysis** — Optional local LLM integration for evidence-based answers with citations
- **Dark-themed GUI** — Professional desktop interface with real-time ingest progress
- **CLI tools** — Command-line ingestor and query tool for power users
- **MCP server** — Expose your legal database to Claude Desktop as a tool
- **Export results** — Copy or save query results to text files
- **In-app auto-update** — Checks GitHub for new releases on startup and offers a one-click download
- **100% local** — Everything runs on your machine. No cloud, no API keys, no data leaving your network.

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
install.bat        # installs dependencies + creates a desktop shortcut
```

Or install dependencies manually with `pip install -r requirements.txt`.

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
- **Windows 10/11** (primary target), Linux/macOS should work with minor adjustments
- **4 GB RAM minimum**, 8+ GB recommended for large collections
- **Disk space**: ~1.5 GB per 100K document chunks indexed

### Optional Dependencies

| Component | Purpose | Download |
|-----------|---------|----------|
| **Tesseract** | OCR for scanned PDFs | [GitHub](https://github.com/UB-Mannheim/tesseract/wiki) |
| **Poppler** | PDF-to-image conversion for OCR | [GitHub](https://github.com/oschwartz10612/poppler-windows/releases) |
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
  create_shortcut.bat  # Creates a desktop shortcut
  install.bat          # One-click installer
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
┌─────────────────────┐
│  Ollama (optional)   │  AI analysis with source citations
└─────────────────────┘
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
