"""Unit tests for knowledge_agent.retrieve — mocked model + collection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import backend.knowledge_agent.retrieve as rt


@pytest.fixture(autouse=True)
def _mock_model_and_collection(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.1, 0.2, 0.3]]
    mock_coll = MagicMock()
    monkeypatch.setattr(rt, "_model", mock_model)
    monkeypatch.setattr(rt, "_collection", mock_coll)
    return mock_model, mock_coll


def test_search_docs_returns_expected_shape(
    _mock_model_and_collection: tuple[MagicMock, MagicMock],
) -> None:
    _, mock_coll = _mock_model_and_collection
    mock_coll.query.return_value = {
        "documents": [["chunk one text", "chunk two text"]],
        "metadatas": [
            [
                {"source": "isr1100-sw-config.pdf", "section": "Hardware Overview"},
                {"source": "isr1100-sw-config.pdf", "section": "Configuring VLANs"},
            ]
        ],
        "distances": [[0.12, 0.34]],
    }
    result = rt.search_docs("how do I change hostname", top_k=2)
    assert result["query"] == "how do I change hostname"
    assert len(result["results"]) == 2
    r0 = result["results"][0]
    assert r0["source"] == "isr1100-sw-config.pdf"
    assert r0["section"] == "Hardware Overview"
    assert r0["text"] == "chunk one text"
    assert r0["score"] == pytest.approx(0.88, abs=0.001)


def test_search_docs_passes_top_k_to_chroma(
    _mock_model_and_collection: tuple[MagicMock, MagicMock],
) -> None:
    _, mock_coll = _mock_model_and_collection
    mock_coll.query.return_value = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    rt.search_docs("foo", top_k=3)
    call_kwargs = mock_coll.query.call_args.kwargs
    assert call_kwargs["n_results"] == 3


def test_search_docs_empty_result(_mock_model_and_collection: tuple[MagicMock, MagicMock]) -> None:
    _, mock_coll = _mock_model_and_collection
    mock_coll.query.return_value = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
    result = rt.search_docs("nothing matches", top_k=5)
    assert result["results"] == []


def test_search_docs_handles_missing_metadata(
    _mock_model_and_collection: tuple[MagicMock, MagicMock],
) -> None:
    _, mock_coll = _mock_model_and_collection
    mock_coll.query.return_value = {
        "documents": [["text"]],
        "metadatas": [[None]],
        "distances": [[0.5]],
    }
    result = rt.search_docs("q", top_k=1)
    assert result["results"][0]["source"] == ""
    assert result["results"][0]["section"] == ""
