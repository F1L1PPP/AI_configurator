"""Unit tests for orchestration.planner — mocked Anthropic, no real LLM call."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.orchestration.confirmations import _reset_for_testing
from backend.orchestration.planner import (
    MAX_ITERATIONS,
    PlannerResult,
    run_planner,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_testing()
    yield
    _reset_for_testing()


# ---------------------------------------------------------------------------
# Helpers to build fake Anthropic responses
# ---------------------------------------------------------------------------


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(tool_name: str, tool_input: dict, block_id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use",
        id=block_id,
        name=tool_name,
        input=tool_input,
    )


def _response(*blocks, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


def _client_with_responses(*responses) -> MagicMock:
    """Build a mock Anthropic client that returns the given responses in order."""
    client = MagicMock()
    client.messages.create.side_effect = list(responses)
    return client


# ---------------------------------------------------------------------------
# End-turn — no tools called
# ---------------------------------------------------------------------------


def test_plain_text_response_no_tool_use():
    client = _client_with_responses(
        _response(_text_block("Hello, ako ti pomôžem?"), stop_reason="end_turn"),
    )
    result = run_planner("ahoj", client=client)
    assert isinstance(result, PlannerResult)
    assert "ahoj" not in result.final_text.lower()  # model response, not echo
    assert result.final_text == "Hello, ako ti pomôžem?"
    assert result.stop_reason == "end_turn"


def test_history_contains_user_and_assistant_messages():
    client = _client_with_responses(
        _response(_text_block("hi"), stop_reason="end_turn"),
    )
    result = run_planner("hello", client=client)
    assert len(result.messages) == 2
    assert result.messages[0]["role"] == "user"
    assert result.messages[0]["content"] == "hello"
    assert result.messages[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Tool use — one round trip
# ---------------------------------------------------------------------------


def test_tool_use_executes_then_continues(monkeypatch):
    # First response: model wants to call show_version
    # Second response: model has the result and produces final text
    first = _response(
        _tool_use_block("show_version", {}, "tu_a"),
        stop_reason="tool_use",
    )
    second = _response(
        _text_block("IOS XE 17.6 on C1111."),
        stop_reason="end_turn",
    )
    client = _client_with_responses(first, second)

    # Stub the actual tool so no SSH happens
    monkeypatch.setattr(
        "backend.orchestration.tool_registry._TOOL_FUNCS",
        {"show_version": lambda: {"version": "17.6"}},
    )

    result = run_planner("show me the version", client=client)
    assert result.final_text == "IOS XE 17.6 on C1111."
    assert result.stop_reason == "end_turn"

    # Event trace must include tool_call + tool_result
    kinds = [ev.kind for ev in result.events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds


# ---------------------------------------------------------------------------
# Awaiting approval event surfaces from propose_* tools
# ---------------------------------------------------------------------------


def test_awaiting_approval_event_emitted_for_propose_tool():
    first = _response(
        _tool_use_block("propose_set_hostname", {"new_name": "LAB-R1"}, "tu_b"),
        stop_reason="tool_use",
    )
    second = _response(
        _text_block("Návrh pripravený. Schváľ ho v /preview."),
        stop_reason="end_turn",
    )
    client = _client_with_responses(first, second)

    result = run_planner("change hostname to LAB-R1", client=client)
    approval_events = [ev for ev in result.events if ev.kind == "awaiting_approval"]
    assert len(approval_events) == 1
    assert approval_events[0].data["action_id"].startswith("act_")
    assert "LAB-R1" in approval_events[0].data["preview"]


# ---------------------------------------------------------------------------
# Iteration cap
# ---------------------------------------------------------------------------


def test_iteration_cap_stops_runaway_loop(monkeypatch):
    """If the model keeps calling tools forever, the planner stops at MAX_ITERATIONS."""
    # Every response is a tool_use (never end_turn)
    loop_response = _response(
        _tool_use_block("show_version", {}, "tu_loop"),
        stop_reason="tool_use",
    )
    client = _client_with_responses(*([loop_response] * (MAX_ITERATIONS + 2)))

    monkeypatch.setattr(
        "backend.orchestration.tool_registry._TOOL_FUNCS",
        {"show_version": lambda: {"v": "17.6"}},
    )

    result = run_planner("loop", client=client)
    assert result.stop_reason == "iteration_cap"
    assert any(ev.kind == "error" for ev in result.events)
    # Should have called create exactly MAX_ITERATIONS times
    assert client.messages.create.call_count == MAX_ITERATIONS


# ---------------------------------------------------------------------------
# History pass-through for follow-up turns
# ---------------------------------------------------------------------------


def test_history_is_passed_through_on_followup():
    prior_history = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": [{"type": "text", "text": "first reply"}]},
    ]
    client = _client_with_responses(
        _response(_text_block("second reply"), stop_reason="end_turn"),
    )
    result = run_planner("second message", history=prior_history, client=client)
    assert result.messages[0] == prior_history[0]
    assert result.messages[1] == prior_history[1]
    assert result.messages[2]["role"] == "user"
    assert result.messages[2]["content"] == "second message"
