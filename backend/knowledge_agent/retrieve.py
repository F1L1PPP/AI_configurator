"""Semantic search over the Cisco doc vector store.

Public API: search_docs(query, top_k=5) -> dict.

The embedding model + Chroma client + collection are loaded lazily on first
call to keep import cost low. The heavy deps (`chromadb`, `sentence_transformers`,
and transitively `torch`) are also imported lazily inside `_ensure_loaded()`,
so just importing this module does NOT pull torch into the process — only
the first `search_docs(...)` call does. Critical for FastAPI startup time
and for any worker that doesn't use RAG.
"""

from __future__ import annotations

import html
import threading
import time
from typing import TYPE_CHECKING, Any

import structlog

from backend.core.settings import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = structlog.get_logger(__name__)

_model: SentenceTransformer | None = None
_collection: Any | None = None
_load_lock = threading.Lock()


class RagNotInitialized(RuntimeError):
    """Raised if search_docs is called before _ensure_loaded() succeeded."""


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
        # Imports stay inside the function so module import doesn't pull
        # torch/chromadb into every process (FastAPI startup, CLI helpers,
        # tests that monkeypatch _model directly).
        import chromadb
        from sentence_transformers import SentenceTransformer

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
    The text field is wrapped in <doc_chunk source="..." section="...">...</doc_chunk>
    tags so the LLM treats chunk content as reference data, not directives.
    """
    t0 = time.perf_counter()
    _ensure_loaded()
    # Explicit runtime check rather than `assert` — assertions are stripped
    # under `python -O`, which would turn a loading failure into a confusing
    # AttributeError later instead of a clear error here.
    if _model is None or _collection is None:
        raise RagNotInitialized(
            "search_docs called but _ensure_loaded() left _model or _collection unset"
        )

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
        source = (meta or {}).get("source", "")
        section = (meta or {}).get("section", "")
        source_attr = html.escape(source, quote=True)
        section_attr = html.escape(section, quote=True)
        wrapped = f'<doc_chunk source="{source_attr}" section="{section_attr}">{doc}</doc_chunk>'
        results.append(
            {
                "source": source,
                "section": section,
                "text": wrapped,
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
