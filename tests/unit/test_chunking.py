"""Unit tests for knowledge_agent.chunking."""

from __future__ import annotations

import pytest

from backend.knowledge_agent.chunking import Chunk, _is_heading, chunk_text


def test_empty_text_returns_no_chunks() -> None:
    assert chunk_text("", source="empty.pdf") == []
    assert chunk_text("   \n\n  ", source="empty.pdf") == []


def test_short_text_yields_single_chunk() -> None:
    text = "hello world from cisco"
    chunks = chunk_text(text, source="t.pdf", chunk_tokens=50, chunk_overlap=5)
    assert len(chunks) == 1
    assert chunks[0].tok_count == 4
    assert chunks[0].text == text
    assert chunks[0].source == "t.pdf"
    assert chunks[0].section == "(no section)"


def test_markdown_headings_become_section_metadata() -> None:
    text = "\n".join(
        [
            "## Hardware Overview",
            "The C1111 has four GigabitEthernet interfaces named Gi0/0/0 through Gi0/0/3.",
            "## Basic Router Configuration",
            "Use the hostname command to set the device name persistently in the running config.",
        ]
    )
    chunks = chunk_text(text, source="m.pdf", chunk_tokens=10, chunk_overlap=2)
    sections = {c.section for c in chunks}
    assert "Hardware Overview" in sections
    assert "Basic Router Configuration" in sections


def test_overlap_between_chunks() -> None:
    words = [f"w{i}" for i in range(100)]
    text = " ".join(words)
    chunks = chunk_text(text, source="t.pdf", chunk_tokens=20, chunk_overlap=5)
    assert len(chunks) >= 4
    first_last_5 = chunks[0].text.split()[-5:]
    second_first_5 = chunks[1].text.split()[:5]
    assert first_last_5 == second_first_5


def test_chunk_id_is_stable() -> None:
    chunks_a = chunk_text("foo bar baz", source="x.pdf", chunk_tokens=10, chunk_overlap=2)
    chunks_b = chunk_text("foo bar baz", source="x.pdf", chunk_tokens=10, chunk_overlap=2)
    assert chunks_a[0].id == chunks_b[0].id


def test_chunk_id_differs_per_source() -> None:
    a = chunk_text("foo bar", source="x.pdf", chunk_tokens=10, chunk_overlap=2)
    b = chunk_text("foo bar", source="y.pdf", chunk_tokens=10, chunk_overlap=2)
    assert a[0].id != b[0].id


def test_invalid_overlap_raises() -> None:
    with pytest.raises(ValueError):
        chunk_text("anything", source="x.pdf", chunk_tokens=10, chunk_overlap=10)


def test_chunk_model_roundtrip() -> None:
    c = Chunk(id="abc", source="s.pdf", section="Sec", text="hi", tok_count=1)
    assert c.model_dump()["section"] == "Sec"


def test_heading_detector_recognizes_cisco_patterns() -> None:
    assert _is_heading("## Hardware Overview")
    assert _is_heading("Chapter 5: Web User Interface")
    assert _is_heading("5.1 Logging In")
    assert _is_heading("Configuring VLANs")
    assert not _is_heading("the quick brown fox jumps over the lazy dog")
    assert not _is_heading("")
    assert not _is_heading("a")
    assert not _is_heading("x" * 200)
