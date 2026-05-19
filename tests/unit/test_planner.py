"""Unit tests for orchestration.planner — mocked Anthropic, no real LLM call."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.orchestration.planner import (
    MAX_ITERATIONS,
    PlannerResult,
    _load_navigation_map,
    run_planner,
)

# _clean_actions fixture is now in tests/conftest.py (autouse).


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


# ---------------------------------------------------------------------------
# Navigation map — _load_navigation_map() unit tests
# ---------------------------------------------------------------------------

_SYNTHETIC_CATALOG = json.dumps(
    {
        "catalog_timestamp": "2026-01-01T00:00:00Z",
        "pages": [
            {
                "url": "https://192.168.10.1/webui/#/staticRouting",
                "title": "Static Routing",
                "hint": "Configuration → Routing Protocols → Static Routing",
            },
            {
                "url": "https://192.168.10.1/webui/#/ospf",
                "title": "OSPF",
                "hint": "Configuration → Routing Protocols → OSPF",
            },
        ],
    }
)


def test_navigation_map_loads_from_catalog():
    """Synthetic 2-page catalog produces a Markdown block with both entries."""
    with patch.object(Path, "read_text", return_value=_SYNTHETIC_CATALOG):
        result = _load_navigation_map()

    assert "## Cisco WebUI navigation map" in result
    assert "/webui/#/staticRouting" in result
    assert "Static Routing" in result
    assert "/webui/#/ospf" in result
    assert "OSPF" in result
    assert "Configuration → Routing Protocols → Static Routing" in result


def test_navigation_map_empty_on_missing_file():
    """Missing catalog file → empty string (graceful degradation)."""
    with patch.object(Path, "read_text", side_effect=FileNotFoundError("no file")):
        result = _load_navigation_map()

    assert result == ""


def test_navigation_map_empty_on_malformed_json():
    """Malformed JSON → empty string (graceful degradation)."""
    with patch.object(Path, "read_text", return_value="{ bad json"):
        result = _load_navigation_map()

    assert result == ""


def test_system_prompt_includes_nav_map_when_loaded():
    """SYSTEM_PROMPT must contain the nav-map heading and a real catalog URL.

    Skipped when the catalog file doesn't exist on this machine.
    """
    catalog_path = Path("knowledge_base/webui-catalog/current.json")
    if not catalog_path.exists():
        pytest.skip("catalog file not present — skipping integration check")

    from backend.orchestration.planner import SYSTEM_PROMPT

    assert "## Cisco WebUI navigation map" in SYSTEM_PROMPT
    assert "/webui/#/staticRouting" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Change 4 — Rule 8: errors from propose_webui_configure are FINAL
# ---------------------------------------------------------------------------


def test_system_prompt_has_errors_final_rule():
    """SYSTEM_PROMPT must contain Rule 8 locking down both propose_webui_configure
    AND propose_cli_configure retries."""
    from backend.orchestration.planner import SYSTEM_PROMPT

    assert "propose_webui_configure" in SYSTEM_PROMPT
    assert "propose_cli_configure" in SYSTEM_PROMPT
    assert "FINAL" in SYSTEM_PROMPT
    # Both tools must appear together in the rule preamble, not in unrelated
    # tool listings. Loose check: the FINAL keyword sits near both names.
    final_idx = SYSTEM_PROMPT.index("FINAL")
    window = SYSTEM_PROMPT[max(0, final_idx - 200) : final_idx + 200]
    assert "propose_webui_configure" in window
    assert "propose_cli_configure" in window


def test_system_prompt_has_per_turn_quota_for_propose_tools():
    """Regression guard: outer Haiku opened Chromium 4× in one turn by
    calling propose_webui_configure with successively tweaked webui_paths
    after each empty-plan response. Rule 8 must explicitly cap each
    propose_* tool at ONE call per turn."""
    from backend.orchestration.planner import SYSTEM_PROMPT

    # Hard cap language must be in the prompt
    assert "Hard quota" in SYSTEM_PROMPT
    # Both tools named explicitly with their once-per-turn limit
    quota_idx = SYSTEM_PROMPT.index("Hard quota")
    window = SYSTEM_PROMPT[quota_idx : quota_idx + 1000]
    assert "ONE call to `propose_webui_configure`" in window
    assert "ONE call to `propose_cli_configure`" in window
    # The fictional webui_path-tweaking pattern that triggered the bug
    # must be called out as wrong.
    assert "/webui/#/OSPF" in window or "tweaked" in window
    # verify_failed responses must also be FINAL
    assert "verify_failed" in window


# ---------------------------------------------------------------------------
# Language detection — the planner replies in the user's language, not a
# hardcoded default. Filip reported (2026-05-19) that the assistant always
# answered in Slovak regardless of the user's input language; root cause was
# the explicit "Speak Slovak by default" line in the system prompt.
# ---------------------------------------------------------------------------


def test_system_prompt_does_not_force_slovak_default():
    """Regression guard for the 2026-05-19 language bug. The literal
    'Speak Slovak by default' phrasing biased the model to Slovak replies
    even on English input. The new rule must be language-symmetric."""
    from backend.orchestration.planner import SYSTEM_PROMPT

    assert "Speak Slovak by default" not in SYSTEM_PROMPT
    # The Slovak-only safety paragraph was also a Slovak bias signal — it
    # taught the model that safety-critical context arrives in Slovak.
    assert "Bezpečnosť:" not in SYSTEM_PROMPT


def test_system_prompt_mirrors_user_language():
    """The new rule must tell the model to detect + mirror the user's
    language, not pin one as default."""
    from backend.orchestration.planner import SYSTEM_PROMPT

    assert "Detect the language" in SYSTEM_PROMPT
    assert "same language" in SYSTEM_PROMPT
    # English fallback for genuinely ambiguous input (single tokens, IDs)
    # must still be specified so the model has a tie-breaker.
    assert "ambiguous" in SYSTEM_PROMPT
    # The Slovak intent-recognition keywords (vykonaj, schválená, cez WebUI,
    # etc.) must STAY — those are user-input triggers, not output bias.
    assert "vykonaj" in SYSTEM_PROMPT
    assert "cez WebUI" in SYSTEM_PROMPT
