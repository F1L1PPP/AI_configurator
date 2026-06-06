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
    """Inner prompt must forbid the inner planner from clicking sidebar
    links or other navigation elements to reach a different page —
    navigation is the outer planner's job via webui_path. Note: clicking
    an in-page Add/Create button to OPEN a form (Static Routing, OSPF
    list pages) is NOT navigation and IS allowed (handled by rule 3)."""
    assert "Do NOT attempt to navigate via sidebar/menu clicks" in _INNER_SYSTEM_PROMPT


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


def test_inner_prompt_requires_click_add_when_button_visible():
    """Regression guard: inner prompt was returning empty plan on the
    OSPF list page even when an 'Add' button was visible — the outer
    Haiku then told the user 'WebUI can't click Add automatically' which
    is wrong (the multi-propose chain handles exactly that). Prompt must
    explicitly tell the inner planner: 'add/create' intent + 'Add' button
    visible → draft [click Add], not empty plan."""
    # The new rule names the click-Add-first pattern explicitly
    assert "add" in _INNER_SYSTEM_PROMPT.lower()
    assert "Add Process" in _INNER_SYSTEM_PROMPT
    assert "DRAFT A SINGLE-STEP PLAN" in _INNER_SYSTEM_PROMPT
    # The OSPF-list example is in the prompt
    assert "Opens the OSPF Add form" in _INNER_SYSTEM_PROMPT


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


# ---------------------------------------------------------------------------
# Fix 1 — forced tool-use JSON path (no prose fallback on normal path)
# ---------------------------------------------------------------------------


def _make_tool_use_client(plan_input: dict) -> MagicMock:
    """Build a mock client whose messages.create returns a tool_use block
    for the submit_plan tool, simulating the forced-tool-choice path."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "submit_plan"
    tool_block.input = plan_input
    mock_response.content = [tool_block]
    mock_client.messages.create.return_value = mock_response
    return mock_client


def test_draft_plan_uses_tool_use_on_normal_path():
    """Normal path: model returns a tool_use block → result parsed from
    block.input, no prose extraction. draft_plan_recovered_from_prose must
    NOT be logged."""
    payload = {
        "plan": [
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": "Pool Name"},
                "value": "DHCP_POOL",
            }
        ],
        "verify_text": "DHCP_POOL",
        "risk": "Creates DHCP pool.",
        "equivalent_cli_commands": ["ip dhcp pool DHCP_POOL"],
    }
    client = _make_tool_use_client(payload)

    result = draft_plan(
        intent="create DHCP pool named DHCP_POOL",
        rag_chunks=[],
        view={"elements": [{"role": "textbox", "name": "Pool Name"}]},
        client=client,
    )

    assert result["plan"][0]["intent"]["name"] == "Pool Name"
    assert result["verify_text"] == "DHCP_POOL"
    assert result["equivalent_cli_commands"] == ["ip dhcp pool DHCP_POOL"]


def test_draft_plan_tool_use_call_includes_tools_and_tool_choice():
    """The Anthropic call must include tools= and tool_choice= forcing
    submit_plan — confirms the API call shape, not just the output."""
    payload = {
        "plan": [],
        "verify_text": None,
        "risk": "noop",
        "equivalent_cli_commands": [],
    }
    client = _make_tool_use_client(payload)

    draft_plan(intent="test", rag_chunks=[], view={}, client=client)

    call_kwargs = client.messages.create.call_args.kwargs
    assert "tools" in call_kwargs, "tools= must be passed to messages.create"
    assert "tool_choice" in call_kwargs, "tool_choice= must be passed to messages.create"
    assert call_kwargs["tool_choice"]["type"] == "tool"
    assert call_kwargs["tool_choice"]["name"] == "submit_plan"
    # The tool list must contain exactly the submit_plan definition
    tool_names = [t["name"] for t in call_kwargs["tools"]]
    assert "submit_plan" in tool_names


def test_draft_plan_prose_fallback_still_parses():
    """Fallback path: model returns a text block instead of tool_use
    (should not happen with forced tool_choice, but the safety net must
    still extract JSON from prose and return a valid dict.)"""
    payload = {
        "plan": [{"action": "click", "intent": {"role": "button", "name": "Apply"}, "value": None}],
        "verify_text": "Applied",
        "risk": "Low.",
        "equivalent_cli_commands": [],
    }
    prose = f"Here is the plan:\n{json.dumps(payload)}\nDone."
    # Use text-only mock (no tool_use block)
    client = _make_mock_client(prose)

    result = draft_plan(intent="apply config", rag_chunks=[], view={}, client=client)

    assert len(result["plan"]) == 1
    assert result["plan"][0]["action"] == "click"
    assert result["verify_text"] == "Applied"


# ---------------------------------------------------------------------------
# Fix 5a — empty/invalid step filtering
# ---------------------------------------------------------------------------


def test_draft_plan_drops_step_with_empty_name():
    """A step where intent.name is an empty string must be silently dropped."""
    payload = {
        "plan": [
            # valid step
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": "Pool Name"},
                "value": "POOL1",
            },
            # invalid step — name is empty string
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": ""},
                "value": "bad",
            },
        ],
        "verify_text": "POOL1",
        "risk": "Creates pool.",
        "equivalent_cli_commands": [],
    }
    client = _make_tool_use_client(payload)

    result = draft_plan(intent="add pool", rag_chunks=[], view={}, client=client)

    assert len(result["plan"]) == 1
    assert result["plan"][0]["intent"]["name"] == "Pool Name"


def test_draft_plan_drops_step_with_missing_role():
    """A step where intent.role is missing (None / absent) must be dropped."""
    payload = {
        "plan": [
            # invalid step — role is None
            {
                "action": "click",
                "intent": {"role": None, "name": "Add"},
                "value": None,
            },
            # valid step
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": "Network Address"},
                "value": "10.0.0.0",
            },
        ],
        "verify_text": None,
        "risk": "Fills network.",
        "equivalent_cli_commands": [],
    }
    client = _make_tool_use_client(payload)

    result = draft_plan(intent="fill network", rag_chunks=[], view={}, client=client)

    assert len(result["plan"]) == 1
    assert result["plan"][0]["intent"]["role"] == "textbox"


def test_draft_plan_drops_step_with_whitespace_only_name():
    """A step where intent.name is purely whitespace must be treated as
    invalid and dropped (the failing DHCP run had textbox||... steps where
    name was effectively blank after separator stripping)."""
    payload = {
        "plan": [
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": "   "},
                "value": "192.168.1.0",
            }
        ],
        "verify_text": None,
        "risk": "No valid steps.",
        "equivalent_cli_commands": [],
    }
    client = _make_tool_use_client(payload)

    result = draft_plan(intent="fill subnet", rag_chunks=[], view={}, client=client)

    # All steps invalid → can't-map signal
    assert result["plan"] == []
    assert result["verify_text"] is None
    assert "Cannot map" in result["risk"]


def test_draft_plan_all_invalid_steps_returns_cant_map_signal():
    """If every step is invalid (empty role/name), draft_plan must return the
    standard can't-map signal: plan=[], verify_text=None, and a risk note
    explaining the validation failure. Callers already handle this shape."""
    payload = {
        "plan": [
            {"action": "fill", "intent": {"role": "", "name": ""}, "value": "x"},
            {"action": "click", "intent": {"role": "", "name": "  "}, "value": None},
        ],
        "verify_text": "should be ignored",
        "risk": "should be replaced",
        "equivalent_cli_commands": ["some cmd"],
    }
    client = _make_tool_use_client(payload)

    result = draft_plan(intent="do something", rag_chunks=[], view={}, client=client)

    assert result["plan"] == []
    assert result["verify_text"] is None
    assert "Cannot map" in result["risk"]
    # equivalent_cli_commands must be empty when we return the cant-map signal
    assert result["equivalent_cli_commands"] == []


# ---------------------------------------------------------------------------
# Nit #6 — truncation guard (stop_reason == "max_tokens")
# ---------------------------------------------------------------------------


def _make_truncated_client() -> MagicMock:
    """Build a mock client whose response simulates a max_tokens truncation."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.stop_reason = "max_tokens"
    # Content is intentionally incomplete — the guard should fire before any
    # attempt to inspect the blocks.
    mock_response.content = []
    mock_client.messages.create.return_value = mock_response
    return mock_client


def test_draft_plan_raises_on_max_tokens_truncation():
    """If stop_reason is 'max_tokens' the function must raise RuntimeError
    rather than trying to parse a potentially broken partial block.input."""
    client = _make_truncated_client()

    with pytest.raises(RuntimeError, match="max_tokens"):
        draft_plan(
            intent="configure many routes",
            rag_chunks=[],
            view={},
            client=client,
        )


def test_draft_plan_normal_stop_reason_does_not_raise():
    """stop_reason == 'tool_use' (normal forced-tool path) must not trigger
    the truncation guard."""
    payload = {
        "plan": [],
        "verify_text": None,
        "risk": "noop",
        "equivalent_cli_commands": [],
    }
    client = _make_tool_use_client(payload)
    # _make_tool_use_client returns a MagicMock; stop_reason is MagicMock()
    # which is truthy but != "max_tokens" — guard must not fire.
    client.messages.create.return_value.stop_reason = "tool_use"

    result = draft_plan(intent="test", rag_chunks=[], view={}, client=client)
    assert isinstance(result, dict)


def test_draft_plan_empty_plan_from_model_passes_through():
    """An intentionally empty plan (model says 'can't map') must pass through
    unchanged — the filter must not interfere with legitimately empty plans
    returned by the model when no elements match."""
    payload = {
        "plan": [],
        "verify_text": None,
        "risk": "Page mismatch — no form fields visible.",
        "equivalent_cli_commands": [],
    }
    client = _make_tool_use_client(payload)

    result = draft_plan(intent="add route", rag_chunks=[], view={}, client=client)

    # Empty plan from model must be preserved as-is (not replaced by cant-map)
    assert result["plan"] == []
    assert result["risk"] == "Page mismatch — no form fields visible."


# ---------------------------------------------------------------------------
# New rules — combobox/select, one-value-per-field, DHCP range semantics
# ---------------------------------------------------------------------------


def test_inner_prompt_combobox_rule_present():
    """Prompt must instruct the planner to use action='select' for combobox
    elements and forbid using 'fill' on a combobox."""
    assert 'role: "combobox"' in _INNER_SYSTEM_PROMPT
    assert "select" in _INNER_SYSTEM_PROMPT
    assert "NEVER use" in _INNER_SYSTEM_PROMPT
    # Explicit mention of fill being wrong for comboboxes
    assert "fill" in _INNER_SYSTEM_PROMPT and "combobox" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_select_requires_options_label():
    """Prompt must tell the model to pick the value from the element's
    'options' list — not invent it."""
    assert "options" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_one_value_per_field_rule():
    """Prompt must explicitly forbid concatenating two values into one field."""
    assert "One value per field" in _INNER_SYSTEM_PROMPT
    # The concrete bad example (network + mask crammed together) must be present
    assert "192.168.100.0 255.255.255.0" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_dhcp_range_semantics():
    """Prompt must explain Starting ip / Ending ip lease-range semantics and
    how to map an exclusion intent to the correct range."""
    assert "Starting ip" in _INNER_SYSTEM_PROMPT
    assert "Ending ip" in _INNER_SYSTEM_PROMPT
    assert "exclude" in _INNER_SYSTEM_PROMPT.lower()


def test_draft_plan_select_step_passes_through():
    """A valid select step produced by the model (combobox element) must
    survive the invalid-step filter and be returned unchanged."""
    payload = {
        "plan": [
            {
                "action": "select",
                "intent": {"role": "combobox", "name": "IP Version"},
                "value": "IPv4",
            },
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": "Starting ip"},
                "value": "192.168.100.11",
            },
            {
                "action": "fill",
                "intent": {"role": "textbox", "name": "Ending ip"},
                "value": "192.168.100.254",
            },
        ],
        "verify_text": "192.168.100",
        "risk": "Creates DHCP pool with lease range .11–.254.",
        "equivalent_cli_commands": [
            "ip dhcp pool LAN",
            "network 192.168.100.0 255.255.255.0",
        ],
    }
    client = _make_tool_use_client(payload)

    result = draft_plan(
        intent="configure DHCP pool, exclude .1 through .10",
        rag_chunks=[],
        view={
            "elements": [
                {"role": "combobox", "name": "IP Version", "options": ["IPv4", "IPv6"]},
                {"role": "textbox", "name": "Starting ip"},
                {"role": "textbox", "name": "Ending ip"},
            ]
        },
        client=client,
    )

    assert len(result["plan"]) == 3
    select_step = result["plan"][0]
    assert select_step["action"] == "select"
    assert select_step["intent"]["role"] == "combobox"
    assert select_step["value"] == "IPv4"
    # Range steps preserved exactly
    assert result["plan"][1]["intent"]["name"] == "Starting ip"
    assert result["plan"][1]["value"] == "192.168.100.11"
    assert result["plan"][2]["intent"]["name"] == "Ending ip"
    assert result["plan"][2]["value"] == "192.168.100.254"
