"""Unit tests for backend.orchestration.configure_planner (Phase 5)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.orchestration.configure_planner import _INNER_SYSTEM_PROMPT, draft_plan


def _make_mock_client(text: str) -> MagicMock:
    """Build a mock Anthropic client whose messages.create returns `text`."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text=text)]
    mock_client.messages.create.return_value = mock_response
    return mock_client


def test_draft_plan_returns_structured_output():
    """Happy path: mock returns well-formed JSON; assert structure."""
    payload = {
        "plan": [
            {
                "action": "click",
                "intent": {"role": "button", "name": "Add Process"},
                "value": None,
            },
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": "Process ID"},
                "value": "100",
            },
        ],
        "verify_text": "OSPF process 100 enabled",
        "risk": "Enabling OSPF may cause routing changes.",
    }
    client = _make_mock_client(json.dumps(payload))

    result = draft_plan(
        intent="configure OSPF process 100 area 0",
        rag_chunks=[{"text": "OSPF config reference", "source": "ospf.pdf", "section": "OSPF"}],
        view={"elements": [{"role": "button", "name": "Add Process"}]},
        client=client,
    )

    assert len(result["plan"]) == 2
    assert result["plan"][0]["action"] == "click"
    assert result["plan"][1]["value"] == "100"
    assert result["verify_text"] == "OSPF process 100 enabled"
    assert "routing" in result["risk"]


def test_draft_plan_handles_empty_plan():
    """Inner LLM says intent cannot be mapped → empty plan passed through."""
    payload = {
        "plan": [],
        "verify_text": None,
        "risk": "Cannot map intent to current view: OSPF panel not visible",
    }
    client = _make_mock_client(json.dumps(payload))

    result = draft_plan(
        intent="configure OSPF",
        rag_chunks=[],
        view={"elements": []},
        client=client,
    )

    assert result["plan"] == []
    assert result["verify_text"] is None
    assert "Cannot map" in result["risk"]


def test_draft_plan_raises_on_non_json():
    """Non-JSON response from inner LLM → RuntimeError."""
    client = _make_mock_client("I cannot help with that.")

    with pytest.raises(RuntimeError, match="non-JSON"):
        draft_plan(
            intent="configure OSPF",
            rag_chunks=[],
            view={},
            client=client,
        )


def test_draft_plan_raises_on_missing_plan_key():
    """JSON object without 'plan' key → RuntimeError."""
    client = _make_mock_client(json.dumps({"foo": "bar"}))

    with pytest.raises(RuntimeError, match="missing 'plan'"):
        draft_plan(
            intent="configure OSPF",
            rag_chunks=[],
            view={},
            client=client,
        )


# ---------------------------------------------------------------------------
# Prompt-content tests (Phase 5 Sub-task C)
# ---------------------------------------------------------------------------


def test_inner_prompt_forbids_inventing_names():
    """Inner prompt must enforce verbatim describe_page element names."""
    assert "verbatim copy of an entry in the" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_has_refuse_example():
    """Inner prompt must include both the OK-fill example and the refuse/empty-plan example."""
    # Load-bearing phrases from each example block
    assert "Prefix Mask" in _INNER_SYSTEM_PROMPT, "OK output example (form fill) missing"
    assert "Page mismatch" in _INNER_SYSTEM_PROMPT, "Refuse/empty-plan example missing"


def test_inner_prompt_forbids_navigation_in_plan():
    """Inner prompt must state that navigation is the outer planner's responsibility."""
    assert "navigation is the outer planner" in _INNER_SYSTEM_PROMPT
