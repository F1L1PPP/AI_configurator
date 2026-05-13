"""One-shot PDF ingest CLI: chunk + embed + persist to ChromaDB.

Run with:
    python -m backend.knowledge_agent.ingest

Reads every *.pdf in `settings.knowledge_base_dir / "docs"`, chunks each with
heading-aware chunker, embeds with sentence-transformers/all-MiniLM-L6-v2, and
upserts into a persistent ChromaDB collection.

Re-running is idempotent: chunk IDs are deterministic (sha1 of source+offset),
so upsert replaces previous embeddings of the same chunk.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import chromadb
import structlog
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

from backend.core.logging import configure_logging
from backend.core.settings import get_settings
from backend.knowledge_agent.chunking import Chunk, chunk_text

log = structlog.get_logger(__name__)


def _extract_pdf_text(pdf_path: Path) -> tuple[str, int]:
    reader = PdfReader(str(pdf_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages), len(pages)


def _embed_batched(
    model: SentenceTransformer, texts: list[str], batch_size: int = 64
) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vecs = model.encode(batch, show_progress_bar=False, convert_to_numpy=True)
        embeddings.extend([v.tolist() for v in vecs])
    return embeddings


def run_ingest() -> int:
    configure_logging()
    settings = get_settings()
    docs_dir = settings.knowledge_base_dir / "docs"

    if not docs_dir.exists():
        print(f"ERROR: docs directory not found: {docs_dir}", file=sys.stderr)
        print("See docs/rag-sources.md for the curated PDF shortlist.", file=sys.stderr)
        return 1

    pdfs = sorted(docs_dir.glob("*.pdf"))
    if not pdfs:
        print(f"ERROR: no PDFs in {docs_dir}", file=sys.stderr)
        print("Download the curated set listed in docs/rag-sources.md and place", file=sys.stderr)
        print("each *.pdf in this folder, then re-run.", file=sys.stderr)
        return 1

    log.info("ingest_start", pdf_count=len(pdfs), docs_dir=str(docs_dir))

    all_chunks: list[Chunk] = []
    for pdf in pdfs:
        t0 = time.perf_counter()
        text, page_count = _extract_pdf_text(pdf)
        chunks = chunk_text(
            text,
            source=pdf.name,
            chunk_tokens=settings.rag_chunk_tokens,
            chunk_overlap=settings.rag_chunk_overlap,
        )
        log.info(
            "pdf_extracted",
            pdf=pdf.name,
            pages=page_count,
            chunks=len(chunks),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        all_chunks.extend(chunks)

    if not all_chunks:
        print(
            "ERROR: extracted zero chunks from corpus — check PDFs are not encrypted/blank.",
            file=sys.stderr,
        )
        return 2

    log.info("embedding_start", chunks=len(all_chunks), model=settings.embedding_model)
    t0 = time.perf_counter()
    model = SentenceTransformer(settings.embedding_model)
    embeddings = _embed_batched(model, [c.text for c in all_chunks])
    log.info(
        "embedding_done",
        chunks=len(all_chunks),
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )

    settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
    collection = client.get_or_create_collection(settings.chroma_collection)
    collection.upsert(
        ids=[c.id for c in all_chunks],
        embeddings=embeddings,
        documents=[c.text for c in all_chunks],
        metadatas=[{"source": c.source, "section": c.section} for c in all_chunks],
    )

    total_tokens = sum(c.tok_count for c in all_chunks)
    log.info(
        "ingest_complete",
        chunks=len(all_chunks),
        total_tokens=total_tokens,
        collection=settings.chroma_collection,
        persist_dir=str(settings.chroma_persist_dir),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_ingest())
