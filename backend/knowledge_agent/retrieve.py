"""Semantic search over the Cisco doc vector store.

Public API: search_docs(query, top_k=5) -> dict.

The embedding model + Chroma client + collection are loaded lazily on first
call to keep import cost low (especially for processes that don't use RAG).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import chromadb
import structlog
from sentence_transformers import SentenceTransformer

from backend.core.settings import get_settings

log = structlog.get_logger(__name__)

_model: SentenceTransformer | None = None
_collection: Any | None = None
_load_lock = threading.Lock()


def _ensure_loaded() -> None:
    global _model, _collection
    # Fast path — already loaded, no lock needed.
    if _model is not None and _collection is not None:
        return
    # Slow path — serialise the first-time load so two threads can't both
    # construct a SentenceTransformer (~50 MB extra alloc). Double-check
    # inside the lock so we don't pay the lock cost after the first caller
    # has populated the globals.
    with _load_lock:
        if _model is not None and _collection is not None:
            return
        settings = get_settings()
        if _model is None:
            _model = SentenceTransformer(settings.embedding_model)
        if _collection is None:
            client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
            _collection = client.get_or_create_collection(
                settings.chroma_collection,
                metadata={"hnsw:space": "cosine"},
            )


def search_docs(query: str, top_k: int = 5) -> dict[str, Any]:
    """Embed query and return the top_k most similar chunks from the vector store.

    Returns:
        {
            "query": str,
            "results": [
                {"source": str, "section": str, "text": str, "score": float},
                ...
            ],
        }

    score = 1 - cosine_distance (higher = more relevant).
    """
    t0 = time.perf_counter()
    _ensure_loaded()
    assert _model is not None and _collection is not None

    encoded = _model.encode([query], show_progress_bar=False, convert_to_numpy=True)
    first = encoded[0]
    query_emb = first.tolist() if hasattr(first, "tolist") else list(first)
    raw = _collection.query(
        query_embeddings=[query_emb],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    documents = raw.get("documents", [[]])[0]
    metadatas = raw.get("metadatas", [[]])[0]
    distances = raw.get("distances", [[]])[0]

    results = []
    for doc, meta, dist in zip(documents, metadatas, distances, strict=False):
        results.append(
            {
                "source": (meta or {}).get("source", ""),
                "section": (meta or {}).get("section", ""),
                "text": doc,
                "score": round(1.0 - float(dist), 4),
            }
        )

    duration_ms = int((time.perf_counter() - t0) * 1000)
    log.info(
        "tool_call",
        tool="search_docs",
        params={"query": query, "top_k": top_k},
        result_summary=f"{len(results)} hits",
        duration_ms=duration_ms,
    )

    return {"query": query, "results": results}
