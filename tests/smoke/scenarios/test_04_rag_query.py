"""§2 Scenario 4 — RAG: query the Cisco doc corpus with citations.

Uses the same `search_docs` callable the planner invokes. Doesn't need
the router — only needs the vector store to be populated (the test
auto-skips if it isn't).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


def _vectorstore_populated() -> bool:
    """True iff the Chroma collection has at least one chunk."""
    try:
        import chromadb

        from backend.core.settings import get_settings

        s = get_settings()
        client = chromadb.PersistentClient(path=str(s.chroma_persist_dir))
        coll = client.get_or_create_collection(
            s.chroma_collection, metadata={"hnsw:space": "cosine"}
        )
        return coll.count() > 0
    except Exception:
        return False


def test_search_docs_returns_relevant_chunk_for_hostname_query():
    if not _vectorstore_populated():
        pytest.skip(
            "Chroma collection empty. Run `python -m backend.knowledge_agent.ingest` first."
        )

    from backend.knowledge_agent.retrieve import search_docs

    result = search_docs("how do I change the hostname on a Cisco ISR 1100", top_k=3)
    assert result["query"]
    hits = result["results"]
    assert len(hits) >= 1, "expected at least one chunk hit"
    # Top hit must have a positive cosine score (vectors aren't antipodal)
    assert hits[0]["score"] > 0.2, f"top hit too weak: {hits[0]}"


def test_search_docs_returns_chunk_for_vlan_query():
    if not _vectorstore_populated():
        pytest.skip(
            "Chroma collection empty. Run `python -m backend.knowledge_agent.ingest` first."
        )

    from backend.knowledge_agent.retrieve import search_docs

    result = search_docs("where do I add a VLAN on the C1111 CLI", top_k=3)
    assert len(result["results"]) >= 1
    assert result["results"][0]["score"] > 0.2
