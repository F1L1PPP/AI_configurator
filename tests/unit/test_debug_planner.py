"""Unit tests for backend.orchestration.debug_planner."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
from anthropic._exceptions import OverloadedError as AnthropicOverloadedError

from backend.orchestration.debug_planner import (
    _PLANNER_MODEL,
    draft_debug_plan,
    draft_debug_summary,
    draft_debug_sweep,
)


def _make_mock_client(text: str) -> MagicMock:
    """Build a mock Anthropic client whose messages.create returns `text`."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text=text)]
    mock_client.messages.create.return_value = mock_response
    return mock_client


def _make_overloaded_error() -> AnthropicOverloadedError:
    """Build a real OverloadedError with real httpx objects."""
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(
        status_code=529,
        headers={"request-id": "req_debug_planner_test"},
        request=req,
    )
    return AnthropicOverloadedError(
        message="Overloaded",
        response=resp,
        body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )


def test_draft_debug_plan_focused_diagnosis():
    """Feed a verify_failed context for a static-route case; assert returned plan."""
    failure_context = {
        "error": "verify_failed",
        "verify_command": "show ip route static",
        "verify_pattern": "5.5.5.0",
        "verify_output_preview": "Codes: L - local, C - connected ...",
        "device_errors": [],
    }
    payload = {
        "commands": ["show ip route static | include 5.5.5.0"],
        "summary_intent": "Confirm whether the static route to 5.5.5.0 landed",
        "risk": "low — read-only show commands",
    }
    client = _make_mock_client(json.dumps(payload))

    result = draft_debug_plan(failure_context, client=client)

    assert result["commands"] == ["show ip route static | include 5.5.5.0"]
    assert "5.5.5.0" in result["summary_intent"]
    assert result["risk"] == "low — read-only show commands"


def test_draft_debug_sweep_broad():
    """No failure context; mock Anthropic to return 4 commands; assert result."""
    payload = {
        "commands": [
            "show ip interface brief",
            "show ip route summary",
            "show logging | tail 20",
            "show running-config | include hostname",
        ],
        "summary_intent": "Broad health sweep of the C1111",
        "risk": "low — read-only show commands",
    }
    client = _make_mock_client(json.dumps(payload))

    result = draft_debug_sweep(client=client)

    assert len(result["commands"]) == 4
    assert result["commands"][0] == "show ip interface brief"
    assert (
        "health" in result["summary_intent"].lower() or "sweep" in result["summary_intent"].lower()
    )


def test_draft_debug_summary_synthesizes_outputs():
    """Pass outputs dict + failure_context; assert returned digest is non-empty string."""
    outputs = {
        "show ip route static | include 5.5.5.0": "S     5.5.5.0/24 [1/0] via 10.0.0.1",
    }
    failure_context = {
        "verify_command": "show ip route static",
        "verify_pattern": "5.5.5.0",
    }
    digest_text = "The static route to 5.5.5.0 is present in the routing table. The verify pattern was too strict."
    client = _make_mock_client(digest_text)

    result = draft_debug_summary(outputs, failure_context, client=client)

    assert isinstance(result, str)
    assert len(result) > 0
    assert "5.5.5.0" in result or "static" in result.lower()


def test_overloaded_error_returns_fallback_for_plan():
    """When messages.create raises OverloadedError, draft_debug_plan must NOT raise.
    It must return a dict with empty commands and a fallback message."""
    failure_context = {
        "error": "verify_failed",
        "verify_command": "show ip route static",
        "verify_pattern": "5.5.5.0",
        "verify_output_preview": "",
        "device_errors": [],
    }
    client = MagicMock()
    client.messages.create.side_effect = _make_overloaded_error()

    result = draft_debug_plan(failure_context, client=client)

    # Must not raise; must return a dict with empty commands
    assert isinstance(result, dict)
    assert result.get("commands") == []
    # Fallback message should mention manual inspection or similar
    assert isinstance(result.get("summary_intent"), str)
    assert len(result["summary_intent"]) > 0


def test_json_parse_recovery():
    """When the LLM wraps JSON in prose, _extract_first_json_object-style recovery works."""
    payload = {
        "commands": ["show ip route static | include 5.5.5.0"],
        "summary_intent": "Check static route presence",
        "risk": "low — read-only show commands",
    }
    prose = f"Here is my diagnostic plan:\n{json.dumps(payload)}\nThat should help."
    client = _make_mock_client(prose)

    result = draft_debug_plan(
        {"error": "verify_failed", "verify_command": "show ip route static"},
        client=client,
    )

    assert result["commands"] == ["show ip route static | include 5.5.5.0"]


def test_planner_model_is_haiku():
    """Production rule: only claude-haiku-4-5-20251001 for inner LLM calls."""
    assert _PLANNER_MODEL == "claude-haiku-4-5-20251001"
