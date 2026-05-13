"""Regression tests for audit-B4 — _search_docs param whitelisting.

Pre-fix, `_search_docs(**kwargs)` forwarded the planner's tool input
verbatim to the inner retrieve.search_docs. An unknown key would
TypeError, and a multi-megabyte query string would happily go into
SentenceTransformer.encode — a real DoS surface. The new wrapper plucks
named params + caps query length + bounds top_k.
"""

from __future__ import annotations

from backend.orchestration import tool_registry as tr


def test_search_docs_rejects_missing_query():
    result = tr._search_docs()
    assert result["error"] == "bad_parameters"


def test_search_docs_rejects_non_string_query():
    result = tr._search_docs(query=42)
    assert result["error"] == "bad_parameters"


def test_search_docs_rejects_empty_query():
    result = tr._search_docs(query="   ")
    assert result["error"] == "bad_parameters"


def test_search_docs_caps_query_length():
    # 10 kB query — well over the cap
    result = tr._search_docs(query="x" * 10_000)
    assert result["error"] == "bad_parameters"
    assert "too long" in result["message"]


def test_search_docs_rejects_negative_top_k():
    result = tr._search_docs(query="hostname", top_k=-1)
    assert result["error"] == "bad_parameters"


def test_search_docs_rejects_zero_top_k():
    result = tr._search_docs(query="hostname", top_k=0)
    assert result["error"] == "bad_parameters"


def test_search_docs_rejects_huge_top_k():
    result = tr._search_docs(query="hostname", top_k=100_000)
    assert result["error"] == "bad_parameters"


def test_search_docs_rejects_non_int_top_k():
    result = tr._search_docs(query="hostname", top_k="5")
    assert result["error"] == "bad_parameters"


def test_search_docs_rejects_bool_top_k():
    # bool is a subclass of int in Python — must be rejected explicitly
    result = tr._search_docs(query="hostname", top_k=True)
    assert result["error"] == "bad_parameters"


def test_search_docs_ignores_extra_kwargs(monkeypatch):
    """Planner sometimes emits extra fields; the wrapper must silently
    drop them rather than TypeError-ing the inner call."""
    captured = {}

    def fake_search(query, top_k):
        captured["query"] = query
        captured["top_k"] = top_k
        return {"query": query, "results": []}

    # Patch the kb_retrieve module lookup
    import backend.knowledge_agent.retrieve as kb_retrieve

    monkeypatch.setattr(kb_retrieve, "search_docs", fake_search)

    result = tr._search_docs(query="hostname", top_k=3, unexpected_extra="ignored")
    assert captured == {"query": "hostname", "top_k": 3}
    assert "results" in result
