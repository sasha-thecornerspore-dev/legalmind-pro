"""
LegalMind MCP Server — exposes the legal RAG database as tools for Claude Desktop / Cowork.

Tools:
  • search_legal_database  — semantic search with scores, page citations, doc_type filtering
  • list_legal_cases       — show all indexed case collections and their stats
  • search_all_cases       — cross-case search across every collection
  • get_case_stats         — detailed stats for a specific case

Setup:
  1. pip install mcp langchain-huggingface langchain-community chromadb sentence-transformers
  2. Add to Claude Desktop config:
     {
       "mcpServers": {
         "legal-rag": {
           "command": "python",
           "args": ["C:\\Users\\sasha\\Documents\\legal-rag\\legal_rag_mcp.py"]
         }
       }
     }
  3. Restart Claude Desktop
"""

import os, sys, asyncio, warnings
from typing import Any

warnings.filterwarnings("ignore")

# Ensure package root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from core import classify_doc_type
from store import (
    list_cases, query_collection, VECTORS_DIR,
)
import store


server = Server("legal-rag-pro")


# ── Tool Definitions ─────────────────────────────────────────────────────────
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_legal_database",
            description=(
                "Search a specific legal case collection for relevant document chunks. "
                "Returns the top matching text segments with source file, page number, "
                "document type, and relevance score. Use list_legal_cases first to see "
                "available collections."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "case_name": {
                        "type": "string",
                        "description": "The case collection to search (e.g. 'sarah', 'foreclosure')"
                    },
                    "query": {
                        "type": "string",
                        "description": "Natural language search query"
                    },
                    "doc_type": {
                        "type": "string",
                        "description": "Optional: filter by document type",
                        "enum": ["Motion", "Order", "Exhibit", "Affidavit", "Notice",
                                 "Complaint", "Response", "Discovery", "Brief", "Contract", "Other"]
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 8, max 20)",
                        "default": 8
                    }
                },
                "required": ["query", "case_name"]
            }
        ),
        Tool(
            name="list_legal_cases",
            description="List all indexed legal case collections with their chunk counts.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="search_all_cases",
            description=(
                "Search across ALL indexed case collections simultaneously. "
                "Useful when you don't know which case contains the relevant information."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query"
                    },
                    "num_results_per_case": {
                        "type": "integer",
                        "description": "Results per case (default 3)",
                        "default": 3
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_case_stats",
            description="Get detailed statistics for a specific case collection — document types, file list, chunk count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_name": {
                        "type": "string",
                        "description": "The case collection name"
                    }
                },
                "required": ["case_name"]
            }
        ),
    ]


# ── Tool Implementations ─────────────────────────────────────────────────────
@server.call_tool()
async def handle_call_tool(name: str, args: dict[str, Any]) -> list[TextContent]:

    # ── list_legal_cases ─────────────────────────────────────────────────
    if name == "list_legal_cases":
        cases = list_cases()
        if not cases:
            return [TextContent(type="text", text="No case collections found. Run the ingestor to index PDFs.")]
        lines = ["Indexed Legal Cases:\n"]
        for c in cases:
            lines.append(f"  • {c['name']}  —  {c['chunks']:,} chunks")
        return [TextContent(type="text", text="\n".join(lines))]

    # ── search_legal_database ────────────────────────────────────────────
    if name == "search_legal_database":
        case_name = args.get("case_name", "").lower().replace(" ", "_")
        query = args.get("query", "").strip()
        k = min(int(args.get("num_results", 8)), 20)

        if not query:
            return [TextContent(type="text", text="Error: query cannot be empty.")]

        doc_type_filter = args.get("doc_type", "") or None

        try:
            # Direct ChromaDB query (bypasses LangChain wrapper issues)
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: query_collection(case_name, query, k=k,
                                          doc_type_filter=doc_type_filter)
            )

            if not results:
                return [TextContent(type="text",
                    text=f"No results found in '{case_name}' for: '{query}'")]

            parts = [f"LegalRAG: {len(results)} results for '{query}' in '{case_name}'\n"]
            for i, r in enumerate(results, 1):
                fname = r.get("filename", "unknown")
                page = r.get("page", "?")
                dtype = r.get("doc_type", "")
                score = r.get("score", 0)
                score_str = f"{score:.1%}" if isinstance(score, (int, float)) else str(score)

                parts.append(
                    f"[{i}] {fname} | Page {page} | {dtype} | Score: {score_str}\n"
                    f"{r.get('content', '')}"
                )

            return [TextContent(type="text", text="\n\n---\n\n".join(parts))]

        except Exception as e:
            return [TextContent(type="text", text=f"Search Error: {e}")]

    # ── search_all_cases ─────────────────────────────────────────────────
    if name == "search_all_cases":
        query = args.get("query", "").strip()
        k_per = min(int(args.get("num_results_per_case", 3)), 10)

        if not query:
            return [TextContent(type="text", text="Error: query cannot be empty.")]

        cases = list_cases()
        if not cases:
            return [TextContent(type="text", text="No case collections found.")]

        all_parts = [f"Cross-case search for: '{query}'\n"]

        for case_info in cases:
            try:
                results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda cn=case_info["name"]: query_collection(cn, query, k=k_per)
                )

                if results:
                    all_parts.append(f"\n== Case: {case_info['name']} ==")
                    for i, r in enumerate(results, 1):
                        fname = r.get("filename", "unknown")
                        page = r.get("page", "?")
                        dtype = r.get("doc_type", "")
                        score = r.get("score", 0)
                        score_str = f"{score:.1%}" if isinstance(score, (int, float)) else str(score)
                        content = (r.get("content") or "")[:300]
                        all_parts.append(
                            f"  [{i}] {fname} | p.{page} | {dtype} | {score_str}\n"
                            f"  {content}..."
                        )
            except Exception as e:
                all_parts.append(f"\n== Case: {case_info['name']} ==\n  Error: {e}")

        return [TextContent(type="text", text="\n".join(all_parts))]

    # ── get_case_stats ───────────────────────────────────────────────────
    if name == "get_case_stats":
        case_name = args.get("case_name", "")
        try:
            key = store.normalize_case(case_name)
            tbl = store.chunks_table()
            count = tbl.count_rows(f"case_key = '{key}'")
            arr = (tbl.search()
                      .where(f"case_key = '{key}'")
                      .select(["filename", "doc_type"])
                      .limit(None)
                      .to_arrow())

            # Aggregate metadata
            files = {}
            doc_types = {}
            for fname, dtype in zip(arr.column("filename").to_pylist(),
                                    arr.column("doc_type").to_pylist()):
                fname = fname or "unknown"
                dtype = dtype or "Other"
                files[fname] = files.get(fname, 0) + 1
                doc_types[dtype] = doc_types.get(dtype, 0) + 1

            lines = [
                f"Case: {case_name}",
                f"Total chunks: {count:,}",
                f"Files indexed: {len(files)}",
                f"\nDocument Types:",
            ]
            for dt, cnt in sorted(doc_types.items(), key=lambda x: -x[1]):
                lines.append(f"  {dt}: {cnt:,} chunks")
            lines.append(f"\nFiles:")
            for fname, cnt in sorted(files.items()):
                lines.append(f"  {fname} ({cnt:,} chunks)")

            return [TextContent(type="text", text="\n".join(lines))]

        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    raise ValueError(f"Unknown tool: {name}")


# ── Entry Point ──────────────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
