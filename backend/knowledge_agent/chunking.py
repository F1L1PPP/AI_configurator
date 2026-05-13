"""Heading-aware sliding-window chunker for Cisco doc corpus.

Token counts are approximated by word counts. The MiniLM-L6 embedding model
truncates inputs over 256 tokens, so default chunk size in settings is 250.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel

_HEADING_PATTERNS = [
    re.compile(r"^#{1,6}\s+\S"),
    re.compile(r"^Chapter\s+\d+", re.IGNORECASE),
    re.compile(r"^\d+(\.\d+)*\s+[A-Z]"),
    re.compile(r"^(Configuring|About|Overview|Introduction|Understanding|Managing)\s+[A-Z]"),
]


class Chunk(BaseModel):
    id: str
    source: str
    section: str
    text: str
    tok_count: int


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 120:
        return False
    return any(p.match(s) for p in _HEADING_PATTERNS)


def _clean_heading(line: str) -> str:
    return line.strip().lstrip("#").strip()


def _chunk_id(source: str, start: int) -> str:
    # blake2b with 8-byte digest is the right primitive here: non-cryptographic
    # use (deterministic chunk ID for Chroma upsert), avoids the lint noise
    # of truncated-SHA1, and the 64-bit space is fine for one corpus
    # (birthday collision at ~4B chunks; our corpus is ~50k).
    return hashlib.blake2b(f"{source}:{start}".encode(), digest_size=8).hexdigest()


def chunk_text(
    text: str,
    source: str,
    *,
    chunk_tokens: int = 250,
    chunk_overlap: int = 30,
) -> list[Chunk]:
    """Split text into overlapping chunks, tagging each with the most recent heading.

    Args:
        text: raw document text (already extracted from PDF or markdown).
        source: filename of the source document (stored on each chunk).
        chunk_tokens: target chunk size in approximate tokens (word-count proxy).
        chunk_overlap: overlap between consecutive chunks (must be < chunk_tokens).
    """
    if chunk_overlap >= chunk_tokens:
        raise ValueError("chunk_overlap must be < chunk_tokens")
    if not text or not text.strip():
        return []

    words: list[str] = []
    section_marks: list[tuple[int, str]] = [(0, "(no section)")]

    for line in text.splitlines():
        if _is_heading(line):
            section_marks.append((len(words), _clean_heading(line)))
            continue
        words.extend(line.split())

    if not words:
        return []

    def section_at(word_idx: int) -> str:
        current = section_marks[0][1]
        for idx, name in section_marks:
            if idx <= word_idx:
                current = name
            else:
                break
        return current

    chunks: list[Chunk] = []
    step = chunk_tokens - chunk_overlap
    start = 0
    while start < len(words):
        end = min(start + chunk_tokens, len(words))
        slice_words = words[start:end]
        chunks.append(
            Chunk(
                id=_chunk_id(source, start),
                source=source,
                section=section_at(start),
                text=" ".join(slice_words),
                tok_count=len(slice_words),
            )
        )
        if end >= len(words):
            break
        start += step

    return chunks
