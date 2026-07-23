"""
LegalMind CLI Query — interactive evidence interrogator with AI analysis.

Supports multi-case collections, doc_type filtering, relevance scores,
and multiple LLM providers (Ollama, Claude, OpenAI).

Uses direct ChromaDB queries (no LangChain wrapper) for reliable results.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store import list_cases, query_collection
from core import (
    query_llm,
    list_ollama_models, DEFAULT_OLLAMA_MODEL,
    get_analysis_prompt, ANALYSIS_MODES,
)


def pick_case() -> str:
    """Interactive case selector."""
    cases = list_cases()
    if not cases:
        print("No cases found in the database. Run ingest first.")
        sys.exit(1)

    print("\nAvailable cases:")
    for i, c in enumerate(cases, 1):
        print(f"  [{i}] {c['name']}  ({c['chunks']:,} chunks)")

    while True:
        choice = input(f"\nSelect case [1-{len(cases)}]: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(cases):
                return cases[idx]["name"]
        except ValueError:
            # Allow typing the case name directly
            for c in cases:
                if choice.lower() == c["name"].lower():
                    return c["name"]
        print("Invalid selection. Try again.")


def run_query():
    print("=" * 55)
    print("  LegalMind Evidence Interrogator")
    print("  Multi-case | Scored results | AI analysis")
    print("=" * 55)

    case = pick_case()
    print(f"\nActive case: {case}")

    # Check Ollama
    models = list_ollama_models()
    if models:
        print(f"Ollama models available: {', '.join(models)}")
        model = DEFAULT_OLLAMA_MODEL
        if model not in models and models:
            model = models[0]
        print(f"Using model: {model}")
        provider = "ollama"
    else:
        model = None
        provider = None
        print("Ollama not detected -- showing raw evidence only.")
        print("(Set ANTHROPIC_API_KEY or OPENAI_API_KEY for cloud AI analysis.)")

    print(f"\nReady. Type your query (or 'exit' to quit).\n")

    while True:
        try:
            query = input("[QUERY]: ").strip()
            if query.lower() in ("exit", "quit", "q"):
                break
            if not query:
                continue

            print(f"\nSearching '{case}' for: '{query}'...\n")

            results = query_collection(case, query, k=8)

            if not results:
                print("  No relevant evidence found.\n")
                continue

            # Display results
            for i, r in enumerate(results, 1):
                fname = r.get("filename", "unknown")
                page = r.get("page", "?")
                dtype = r.get("doc_type", "")
                score = r.get("score", 0)
                score_str = f"{score:.1%}" if isinstance(score, (int, float)) else str(score)
                content = (r.get("content") or "")[:200].strip()

                print(f"  [{i}] {fname} (p.{page}) -- {dtype} -- relevance: {score_str}")
                print(f"      {content}...")
                print()

            # AI analysis
            if provider and model:
                print("-" * 55)
                print(f"Analyzing with {provider} ({model})...\n")
                context_parts = []
                for r in results:
                    src = r.get("filename", "unknown")
                    pg = r.get("page", "?")
                    context_parts.append(f"[{src} p.{pg}]: {r.get('content', '')}")
                context = "\n\n".join(context_parts)

                prompt = get_analysis_prompt("standard", context, query)
                response = query_llm(prompt, provider=provider, model=model)
                print(response.strip() if response else "No response generated.")
                print("-" * 55)
            print()

        except KeyboardInterrupt:
            print("\n")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    run_query()
