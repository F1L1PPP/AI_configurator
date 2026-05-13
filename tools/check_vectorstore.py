"""Print vector store status: chunk count + a sample of sources/sections.

Usage (run from the worktree root):
    .venv/Scripts/python.exe tools/check_vectorstore.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb  # noqa: E402

from backend.core.settings import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    coll = client.get_or_create_collection(
        settings.chroma_collection,
        metadata={"hnsw:space": "cosine"},
    )
    total = coll.count()
    print(f"collection : {settings.chroma_collection}")
    print(f"persist_dir: {settings.chroma_persist_dir}")
    print(f"total chunks: {total}")
    print()
    if total == 0:
        print("(empty — run `python -m backend.knowledge_agent.ingest` first)")
        return 0

    sample = coll.peek(limit=5)
    print("sample chunks:")
    for id_, meta in zip(sample["ids"], sample["metadatas"], strict=False):
        section = (meta or {}).get("section", "")[:60]
        source = (meta or {}).get("source", "")
        print(f"  {id_} | {source} | {section}")

    sources: dict[str, int] = {}
    all_meta = coll.get(include=["metadatas"]).get("metadatas") or []
    for m in all_meta:
        src = (m or {}).get("source", "?")
        sources[src] = sources.get(src, 0) + 1
    print()
    print("chunks per source:")
    for src, n in sorted(sources.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
