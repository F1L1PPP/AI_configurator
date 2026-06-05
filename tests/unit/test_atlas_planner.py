"""Unit tests for the C2 atlas-typed planner (validate_atlas_plan + draft_atlas_plan)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.orchestration.configure_planner import (
    _ATLAS_SYSTEM_PROMPT,
    draft_atlas_plan,
    validate_atlas_plan,
)
from backend.webui_agent.atlas.schema import FieldSpec, RouteAtlas

# ---------------------------------------------------------------------------
# Helpers — build minimal RouteAtlas fixtures
# ---------------------------------------------------------------------------


def _make_atlas(*fields: FieldSpec) -> RouteAtlas:
    """Build a RouteAtlas containing the given fields (minimal other attrs)."""
    return RouteAtlas(
        route="test/route",
        device_fingerprint="test-fp",
        fields=list(fields),
    )


def _input_field(key: str, required: bool = False) -> FieldSpec:
    return FieldSpec(key=key, label=key, role="textbox", widget="input", required=required)


def _combobox_field(key: str, options: list[str], required: bool = False) -> FieldSpec:
    return FieldSpec(
        key=key,
        label=key,
        role="combobox",
        widget="kendo_combobox",
        required=required,
        options=options,
    )


def _make_atlas_tool_use_client(plan_input: dict) -> MagicMock:
    """Build a mock Anthropic client that returns a submit_atlas_plan tool_use block."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "submit_atlas_plan"
    tool_block.input = plan_input
    mock_response.stop_reason = "tool_use"
    mock_response.content = [tool_block]
    mock_client.messages.create.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# Pure validation tests (no LLM)
# ---------------------------------------------------------------------------


def test_validate_rejects_unknown_field_key():
    """A step whose field_key is absent from the atlas must be dropped with
    reason 'unknown_field_key'."""
    atlas = _make_atlas(_input_field("real.key"))
    plan = [{"field_key": "nonexistent.key", "value": "foo"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    assert valid == []
    assert len(errors) == 1
    assert errors[0]["reason"] == "unknown_field_key"
    assert errors[0]["field_key"] == "nonexistent.key"


def test_validate_rejects_combobox_value_not_in_options():
    """A kendo_combobox step whose value is not in field.options must be
    dropped with reason 'value_not_in_options'."""
    atlas = _make_atlas(_combobox_field("subnet.mask", ["255.255.255.0", "255.255.0.0"]))
    plan = [{"field_key": "subnet.mask", "value": "255.255.128.0"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    assert valid == []
    assert len(errors) == 1
    error = errors[0]
    assert error["reason"] == "value_not_in_options"
    assert error["field_key"] == "subnet.mask"
    assert error["value"] == "255.255.128.0"
    assert "255.255.255.0" in error["options"]


def test_validate_accepts_combobox_value_case_insensitive():
    """A combobox value that differs only in case must be accepted
    (case-insensitive match)."""
    atlas = _make_atlas(_combobox_field("ip.type", ["IPv4", "IPv6"]))
    plan = [{"field_key": "ip.type", "value": "IPV4"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    # No error for wrong-case combobox value
    non_missing_errors = [e for e in errors if e.get("reason") != "missing_required"]
    assert non_missing_errors == []
    assert len(valid) == 1
    assert valid[0]["field_key"] == "ip.type"
    assert valid[0]["value"] == "IPV4"


def test_validate_accepts_plain_input_any_value():
    """An input (textbox) field must accept any value without options checking."""
    atlas = _make_atlas(_input_field("route.prefix"))
    plan = [{"field_key": "route.prefix", "value": "10.99.99.0"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    non_missing_errors = [e for e in errors if e.get("reason") != "missing_required"]
    assert non_missing_errors == []
    assert len(valid) == 1
    assert valid[0]["value"] == "10.99.99.0"


def test_validate_reports_missing_required():
    """A required field that appears in the atlas but has no step in the plan
    must generate a single 'missing_required' info error listing it."""
    atlas = _make_atlas(
        _input_field("route.prefix", required=True),
        _input_field("route.nexthop", required=False),
    )
    # Only fill the optional field — leave the required one empty.
    plan = [{"field_key": "route.nexthop", "value": "192.168.1.1"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    assert len(valid) == 1
    missing_errors = [e for e in errors if e.get("reason") == "missing_required"]
    assert len(missing_errors) == 1
    assert "route.prefix" in missing_errors[0]["fields"]


def test_validate_dedupes_by_field_key():
    """When the same field_key appears more than once, the first occurrence
    wins and subsequent duplicates are silently dropped."""
    atlas = _make_atlas(_input_field("route.prefix"))
    plan = [
        {"field_key": "route.prefix", "value": "10.0.0.0"},
        {"field_key": "route.prefix", "value": "192.168.0.0"},  # duplicate
    ]

    valid, errors = validate_atlas_plan(plan, atlas)

    assert len(valid) == 1
    assert valid[0]["value"] == "10.0.0.0"


def test_validate_empty_plan_no_errors_when_no_required():
    """An empty plan against an atlas with only optional fields should
    produce no errors."""
    atlas = _make_atlas(_input_field("optional.field", required=False))
    valid, errors = validate_atlas_plan([], atlas)

    assert valid == []
    assert errors == []


def test_validate_missing_required_error_only_when_nonempty():
    """The missing_required info error must NOT be emitted when there are no
    missing required fields."""
    atlas = _make_atlas(_input_field("route.prefix", required=True))
    plan = [{"field_key": "route.prefix", "value": "10.0.0.0"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    assert len(valid) == 1
    assert errors == []


def test_validate_accepts_legacy_key_alias():
    """Steps using the legacy 'key' alias instead of 'field_key' must be
    handled correctly."""
    atlas = _make_atlas(_input_field("route.prefix"))
    # Legacy shape: uses "key" not "field_key"
    plan = [{"key": "route.prefix", "value": "10.0.0.0"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    non_missing_errors = [e for e in errors if e.get("reason") != "missing_required"]
    assert non_missing_errors == []
    assert len(valid) == 1
    assert valid[0]["field_key"] == "route.prefix"


def test_validate_combobox_by_role_not_widget():
    """A field with role='combobox' (not widget='kendo_combobox') must also
    enforce options-membership validation."""
    field = FieldSpec(
        key="ip.version",
        label="IP Version",
        role="combobox",  # role-based detection
        widget="input",  # NOT kendo_combobox
        options=["ipv4", "ipv6"],
    )
    atlas = _make_atlas(field)
    plan = [{"field_key": "ip.version", "value": "ipv5"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    assert valid == []
    assert errors[0]["reason"] == "value_not_in_options"


def test_validate_combobox_no_options_skips_check():
    """A kendo_combobox field with no options list (None or empty) must NOT
    enforce options-membership — any value passes."""
    field = FieldSpec(
        key="subnet.mask",
        label="Subnet Mask",
        role="combobox",
        widget="kendo_combobox",
        options=None,  # no options populated yet
    )
    atlas = _make_atlas(field)
    plan = [{"field_key": "subnet.mask", "value": "255.255.255.0"}]

    valid, errors = validate_atlas_plan(plan, atlas)

    non_missing_errors = [e for e in errors if e.get("reason") != "missing_required"]
    assert non_missing_errors == []
    assert len(valid) == 1


def test_validate_combobox_none_value_skipped():
    """P2-validate-plan-typesafe: a None value must be dropped with reason
    'null_value' (NOT coerced to 'none' → value_not_in_options)."""
    atlas = _make_atlas(_combobox_field("subnet.mask", ["255.255.255.0", "255.255.0.0"]))
    plan = [{"field_key": "subnet.mask", "value": None}]

    valid, errors = validate_atlas_plan(plan, atlas)

    assert valid == []
    reasons = [e["reason"] for e in errors if e.get("field_key") == "subnet.mask"]
    assert reasons == ["null_value"], f"expected null_value, got {errors}"


def test_validate_combobox_int_option_no_crash():
    """P2-validate-plan-typesafe: numeric options (e.g. lease days [7, 30]) must
    not crash on opt.strip(); a matching int value passes, a non-matching int is
    dropped cleanly as value_not_in_options."""
    atlas = _make_atlas(_combobox_field("lease.days", [7, 30]))  # type: ignore[list-item]

    valid_ok, errors_ok = validate_atlas_plan(
        [{"field_key": "lease.days", "value": 7}], atlas
    )
    assert len(valid_ok) == 1
    assert valid_ok[0]["value"] == 7

    valid_bad, errors_bad = validate_atlas_plan(
        [{"field_key": "lease.days", "value": 99}], atlas
    )
    assert valid_bad == []
    assert errors_bad[0]["reason"] == "value_not_in_options"


def test_validate_preserves_plan_order():
    """valid_steps must preserve the order of steps from the input plan."""
    atlas = _make_atlas(
        _input_field("field.a"),
        _input_field("field.b"),
        _input_field("field.c"),
    )
    plan = [
        {"field_key": "field.c", "value": "C"},
        {"field_key": "field.a", "value": "A"},
        {"field_key": "field.b", "value": "B"},
    ]

    valid, _ = validate_atlas_plan(plan, atlas)

    assert [s["field_key"] for s in valid] == ["field.c", "field.a", "field.b"]


# ---------------------------------------------------------------------------
# LLM tests (mock the Anthropic client)
# ---------------------------------------------------------------------------


def test_draft_atlas_plan_emits_field_key_plan():
    """Happy path: fake client returns a submit_atlas_plan tool_use with a
    valid field_key plan → draft_atlas_plan returns it with validated steps."""
    atlas = _make_atlas(
        _input_field("route.prefix"),
        _input_field("route.nexthop"),
    )
    view = {
        "route": "routing/static",
        "page_title": "Static Routing",
        "fields": [
            {"key": "route.prefix", "label": "Prefix", "widget": "input", "role": "textbox",
             "required": False, "value": "", "options": None},
            {"key": "route.nexthop", "label": "Next Hop", "widget": "input", "role": "textbox",
             "required": False, "value": "", "options": None},
        ],
    }
    tool_payload = {
        "plan": [
            {"field_key": "route.prefix", "value": "10.99.99.0"},
            {"field_key": "route.nexthop", "value": "192.168.1.1"},
        ],
        "verify_text": "10.99.99.0",
        "risk": "Adds static route.",
        "equivalent_cli_commands": ["ip route 10.99.99.0 255.255.255.0 192.168.1.1"],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    result = draft_atlas_plan(
        intent="add static route 10.99.99.0/24 via 192.168.1.1",
        rag_chunks=[{"text": "Static routes are added via Routing > Static Routing."}],
        view=view,
        atlas=atlas,
        client=client,
    )

    assert len(result["plan"]) == 2
    assert result["plan"][0]["field_key"] == "route.prefix"
    assert result["plan"][0]["value"] == "10.99.99.0"
    assert result["plan"][1]["field_key"] == "route.nexthop"
    assert result["verify_text"] == "10.99.99.0"
    assert result["risk"] == "Adds static route."
    assert result["equivalent_cli_commands"] == [
        "ip route 10.99.99.0 255.255.255.0 192.168.1.1"
    ]
    assert "validation_errors" in result


def test_draft_atlas_plan_drops_invalid_step_via_validation():
    """Fake tool_use includes one unknown field_key + one valid step →
    output plan has only the valid step, validation_errors records the drop."""
    atlas = _make_atlas(_input_field("route.prefix"))
    view = {
        "route": "routing/static",
        "fields": [
            {"key": "route.prefix", "label": "Prefix", "widget": "input", "role": "textbox",
             "required": False, "value": "", "options": None},
        ],
    }
    tool_payload = {
        "plan": [
            {"field_key": "hallucinated.key", "value": "bad"},   # unknown key
            {"field_key": "route.prefix", "value": "10.0.0.0"},  # valid
        ],
        "verify_text": None,
        "risk": "Adds route.",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    result = draft_atlas_plan(
        intent="add route 10.0.0.0/8",
        rag_chunks=[],
        view=view,
        atlas=atlas,
        client=client,
    )

    # Only the valid step survives.
    assert len(result["plan"]) == 1
    assert result["plan"][0]["field_key"] == "route.prefix"

    # validation_errors must record the drop.
    drop_errors = [e for e in result["validation_errors"] if e.get("reason") == "unknown_field_key"]
    assert len(drop_errors) == 1
    assert drop_errors[0]["field_key"] == "hallucinated.key"


def test_draft_atlas_plan_calls_with_correct_tool_name():
    """The Anthropic call must use tool_choice forcing submit_atlas_plan."""
    atlas = _make_atlas(_input_field("field.x"))
    view = {"fields": []}
    tool_payload = {
        "plan": [],
        "verify_text": None,
        "risk": "noop",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    draft_atlas_plan(intent="test", rag_chunks=[], view=view, atlas=atlas, client=client)

    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"]["name"] == "submit_atlas_plan"
    tool_names = [t["name"] for t in call_kwargs["tools"]]
    assert "submit_atlas_plan" in tool_names


def test_draft_atlas_plan_raises_on_max_tokens():
    """stop_reason == 'max_tokens' must raise RuntimeError."""
    atlas = _make_atlas(_input_field("field.x"))
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.stop_reason = "max_tokens"
    mock_response.content = []
    mock_client.messages.create.return_value = mock_response

    with pytest.raises(RuntimeError, match="max_tokens"):
        draft_atlas_plan(
            intent="test",
            rag_chunks=[],
            view={},
            atlas=atlas,
            client=mock_client,
        )


def test_draft_atlas_plan_includes_rag_and_view_in_message():
    """The user message must contain the RAG text and the view JSON."""
    atlas = _make_atlas(_input_field("field.x"))
    view = {"route": "test/route", "fields": []}
    tool_payload = {
        "plan": [],
        "verify_text": None,
        "risk": "noop",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    draft_atlas_plan(
        intent="configure something",
        rag_chunks=[{"text": "UNIQUE_RAG_CONTENT_XYZ"}],
        view=view,
        atlas=atlas,
        client=client,
    )

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "UNIQUE_RAG_CONTENT_XYZ" in sent
    assert "test/route" in sent
    assert "Available fields" in sent


def test_draft_atlas_plan_includes_previous_steps_when_provided():
    """When previous_steps is non-empty, the user message must include
    'Previous steps executed:'."""
    atlas = _make_atlas(_input_field("field.x"))
    tool_payload = {
        "plan": [],
        "verify_text": None,
        "risk": "noop",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    draft_atlas_plan(
        intent="continue config",
        rag_chunks=[],
        view={},
        atlas=atlas,
        client=client,
        previous_steps=[{"step": {"field_key": "field.x"}, "status": "ok"}],
    )

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Previous steps executed:" in sent


def test_draft_atlas_plan_omits_previous_steps_when_none():
    """When previous_steps is None, the user message must NOT include
    'Previous steps executed:'."""
    atlas = _make_atlas(_input_field("field.x"))
    tool_payload = {
        "plan": [],
        "verify_text": None,
        "risk": "noop",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    draft_atlas_plan(
        intent="fresh start",
        rag_chunks=[],
        view={},
        atlas=atlas,
        client=client,
    )

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Previous steps executed:" not in sent


def test_draft_atlas_plan_validation_errors_key_always_present():
    """The returned dict must always have 'validation_errors' key, even when
    the plan is clean."""
    atlas = _make_atlas(_input_field("field.x"))
    tool_payload = {
        "plan": [{"field_key": "field.x", "value": "hello"}],
        "verify_text": None,
        "risk": "fills field",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    result = draft_atlas_plan(
        intent="set field", rag_chunks=[], view={}, atlas=atlas, client=client
    )

    assert "validation_errors" in result
    assert isinstance(result["validation_errors"], list)


# ---------------------------------------------------------------------------
# P1-verify-text-prompt-rule — verify_text passthrough + prompt rule presence
# ---------------------------------------------------------------------------


def test_draft_atlas_plan_passes_string_verify_text_through():
    """A non-null verify_text from the model must pass through unchanged."""
    atlas = _make_atlas(_input_field("pool.name"))
    tool_payload = {
        "plan": [{"field_key": "pool.name", "value": "CORP"}],
        "verify_text": "CORP",
        "risk": "Adds pool CORP.",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    result = draft_atlas_plan(
        intent="add DHCP pool CORP", rag_chunks=[], view={}, atlas=atlas, client=client
    )

    assert result["verify_text"] == "CORP"


def test_draft_atlas_plan_passes_null_verify_text_through():
    """A null verify_text (settings/toggle page) must pass through as None."""
    atlas = _make_atlas(_input_field("toggle.x"))
    tool_payload = {
        "plan": [{"field_key": "toggle.x", "value": "on"}],
        "verify_text": None,
        "risk": "Toggles x.",
        "equivalent_cli_commands": [],
    }
    client = _make_atlas_tool_use_client(tool_payload)

    result = draft_atlas_plan(
        intent="enable x", rag_chunks=[], view={}, atlas=atlas, client=client
    )

    assert result["verify_text"] is None


def test_atlas_system_prompt_documents_verify_text():
    """Lock the verify_text rule's presence in the planner system prompt."""
    assert "verify_text" in _ATLAS_SYSTEM_PROMPT
