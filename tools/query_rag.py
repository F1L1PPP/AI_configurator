"""Spot-check RAG retrieval from the command line.

Usage (run from the worktree root):
    .venv/Scripts/python.exe tools/query_rag.py "your question here"
    .venv/Scripts/python.exe tools/query_rag.py "show vlan brief output" 10

Args:
    1: query (required, wrap in quotes if it has spaces)
    2: top_k (optional, default 5)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.knowledge_agent.retrieve import search_docs  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python tools/query_rag.py "question" [top_k]', file=sys.stderr)
        return 1
    query = sys.argv[1]
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    result = search_docs(query, top_k=top_k)
    print(f"query: {result['query']}")
    print(f"hits:  {len(result['results'])}")
    print()
    for i, hit in enumerate(result["results"], 1):
        text = hit["text"].replace("\n", " ").strip()
        if len(text) > 220:
            text = text[:220] + "..."
        print(f"#{i} [{hit['score']:.3f}] {hit['source']} -- {hit['section']}")
        print(f"    {text}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
