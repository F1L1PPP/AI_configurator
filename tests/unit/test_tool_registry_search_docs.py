"""Verify search_docs is registered as a tool and dispatches correctly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import backend.orchestration.tool_registry as tr


def test_search_docs_in_tool_schemas() -> None:
    names = [t["name"] for t in tr.TOOL_SCHEMAS]
    assert "search_docs" in names
    schema = next(t for t in tr.TOOL_SCHEMAS if t["name"] == "search_docs")
    assert "query" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["query"]


def test_search_docs_in_tool_funcs() -> None:
    assert "search_docs" in tr._TOOL_FUNCS
    assert callable(tr._TOOL_FUNCS["search_docs"])


def test_search_docs_not_in_approval_set() -> None:
    assert "search_docs" not in tr._REQUIRES_APPROVAL


def test_execute_tool_dispatches_search_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_search = MagicMock(
        return_value={
            "query": "hi",
            "results": [{"source": "x.pdf", "section": "S", "text": "t", "score": 0.9}],
        }
    )
    monkeypatch.setitem(tr._TOOL_FUNCS, "search_docs", mock_search)
    result = tr.execute_tool("search_docs", {"query": "how to change hostname"})
    mock_search.assert_called_once_with(query="how to change hostname")
    assert result["query"] == "hi"
    assert len(result["results"]) == 1
