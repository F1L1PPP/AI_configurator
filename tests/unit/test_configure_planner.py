"""Unit tests for backend.orchestration.configure_planner (Phase 5)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.orchestration.configure_planner import (
    _INNER_SYSTEM_PROMPT,
    _PLANNER_MODEL,
    _extract_first_json_object,
    draft_plan,
)


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
    """Inner prompt must forbid the inner planner from clicking sidebar links
    or other navigation elements — navigation is the outer planner's job via
    webui_path. Updated wording (post-retry-loop fix): 'Do NOT attempt to
    navigate via clicks' replaces the older 'navigation is the outer planner'
    phrasing, but the semantic guard is the same."""
    assert "Do NOT attempt to navigate via clicks" in _INNER_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Change 1 — model lock
# ---------------------------------------------------------------------------


def test_planner_model_is_haiku():
    """Inner LLM must be Haiku 4.5 — production-LLM rule (Filip 2026-05-15)."""
    assert _PLANNER_MODEL == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Change 2 — _extract_first_json_object helper
# ---------------------------------------------------------------------------


def test_extract_first_json_object_extracts_clean_json():
    assert _extract_first_json_object('{"a": 1}') == '{"a": 1}'


def test_extract_first_json_object_extracts_from_prose():
    assert (
        _extract_first_json_object('Here is my plan: {"plan": []} hope this helps')
        == '{"plan": []}'
    )


def test_extract_first_json_object_handles_nested():
    text = 'prelude {"plan": [{"nested": true}], "risk": "x"} epilogue'
    result = _extract_first_json_object(text)
    assert result == '{"plan": [{"nested": true}], "risk": "x"}'


def test_extract_first_json_object_handles_braces_in_strings():
    """Braces inside JSON string literals must not confuse the depth counter."""
    text = '{"text": "a } in string {"}'
    result = _extract_first_json_object(text)
    assert result == '{"text": "a } in string {"}'


def test_extract_first_json_object_returns_none_for_no_json():
    assert _extract_first_json_object("just prose no json") is None


# ---------------------------------------------------------------------------
# Change 2 — draft_plan JSON recovery / failure paths
# ---------------------------------------------------------------------------


def test_draft_plan_recovers_from_prose_wrapped_json():
    """If the LLM wraps JSON in prose, draft_plan extracts and parses it."""
    payload = {
        "plan": [{"action": "click", "intent": {"role": "button", "name": "Apply"}, "value": None}],
        "verify_text": "Applied",
        "risk": "Low risk.",
    }
    prose_response = f"Here is my step plan:\n{json.dumps(payload)}\nHope that helps!"
    client = _make_mock_client(prose_response)

    result = draft_plan(
        intent="apply config",
        rag_chunks=[],
        view={},
        client=client,
    )

    assert len(result["plan"]) == 1
    assert result["plan"][0]["action"] == "click"
    assert result["verify_text"] == "Applied"


def test_draft_plan_raises_on_pure_prose():
    """Pure prose with no JSON object must still raise RuntimeError."""
    client = _make_mock_client("The current view shows the Static Routing table page.")

    with pytest.raises(RuntimeError, match="non-JSON"):
        draft_plan(
            intent="add static route",
            rag_chunks=[],
            view={},
            client=client,
        )


# ---------------------------------------------------------------------------
# previous_steps — multi-propose continuation (Phase 5.x)
# ---------------------------------------------------------------------------


def test_draft_plan_passes_previous_steps_to_llm():
    """When previous_steps is non-empty, the user message must include a
    'Previous steps executed:' section so the inner LLM can adapt."""
    payload = {
        "plan": [{"action": "click", "intent": {"role": "button", "name": "Apply"}, "value": None}],
        "verify_text": "Saved",
        "risk": "low",
    }
    client = _make_mock_client(json.dumps(payload))

    draft_plan(
        intent="add static route 10.0.0.0/24",
        rag_chunks=[],
        view={"elements": [{"role": "button", "name": "Apply"}]},
        client=client,
        previous_steps=[
            {
                "iteration": 1,
                "step": {"action": "click", "intent": {"role": "button", "name": "Add"}},
                "result": {"ok": False, "error": "element_not_found"},
                "status": "failed",
            }
        ],
    )

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Previous steps executed:" in sent
    assert "element_not_found" in sent
    assert '"status": "failed"' in sent


def test_draft_plan_omits_previous_steps_when_none():
    """When previous_steps is None or empty, no 'Previous steps executed:'
    section is included (keeps the propose-time prompt unchanged)."""
    payload = {
        "plan": [],
        "verify_text": None,
        "risk": "nope",
    }
    client = _make_mock_client(json.dumps(payload))

    draft_plan(
        intent="add static route",
        rag_chunks=[],
        view={"elements": []},
        client=client,
    )

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Previous steps executed:" not in sent


def test_inner_prompt_documents_previous_steps_rules():
    """Inner prompt must describe how to interpret previous_steps so Haiku
    can adapt to mid-flow failures."""
    assert "Mid-flow continuation" in _INNER_SYSTEM_PROMPT
    assert "previous_steps" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_does_not_invite_caller_to_re_propose():
    """Regression guard: the inner prompt used to instruct the outer Haiku
    to re-propose with a different webui_path when the form wasn't visible.
    That triggered chromium-open loops (4× per turn) directly violating
    outer Rule 8. The empty-plan response must now signal TERMINAL, not
    'try another page'."""
    assert "caller should re-propose" not in _INNER_SYSTEM_PROMPT
    assert "caller will then re-propose" not in _INNER_SYSTEM_PROMPT
    # Replacement language must communicate FINAL
    assert "FINAL" in _INNER_SYSTEM_PROMPT
    assert "TERMINAL" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_documents_cidr_splitting():
    """Regression guard: the previous example mis-mapped 10.0.0.0/24 into
    the 'Prefix Mask' textbox. The corrected example must teach Haiku to
    split CIDR into Prefix + dotted mask across separate fields, and the
    rule must explicitly forbid the broken pattern."""
    # Field-mapping rules section present
    assert "Field-mapping rules" in _INNER_SYSTEM_PROMPT
    # Dotted mask hint for the common /24 case
    assert "255.255.255.0" in _INNER_SYSTEM_PROMPT
    # Explicit instruction to split CIDR across two fields
    assert "split" in _INNER_SYSTEM_PROMPT.lower()
    # Negative example warns against putting CIDR in Prefix Mask
    assert "WRONG" in _INNER_SYSTEM_PROMPT
    assert "Prefix Mask" in _INNER_SYSTEM_PROMPT
