"""Unit tests for the planner's prompt-caching wiring + token telemetry (Phase 1).

These don't talk to the real Anthropic API — we mock the client and verify the
shape of the kwargs we send and the telemetry log we emit. The point is to
catch regressions like "someone removed cache_control" or "telemetry stopped
firing", which won't surface until the next cost spike if untested.

Caching behaviour itself is verified by reading `cache_read_input_tokens` from
real telemetry logs after deploy — that's a manual-eyeball check; this file
just guards the wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import structlog.testing

from backend.orchestration.planner import (
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    _system_prompt_blocks,
    _tools_with_cache_marker,
    run_planner,
)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _response_with_usage(
    *blocks,
    stop_reason: str = "end_turn",
    input_tokens: int = 1234,
    output_tokens: int = 56,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> SimpleNamespace:
    """Fake Anthropic response with both content + usage attached.

    Mirrors the SDK shape (response.usage is a separate object with token
    attributes). The existing test_planner.py fakes lack usage; this test
    file uses richer fakes to assert telemetry.
    """
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
    )
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, usage=usage)


# ---------------------------------------------------------------------------
# Caching wiring
# ---------------------------------------------------------------------------


def test_system_prompt_blocks_carry_cache_control():
    """The system prompt is converted to the list-of-blocks form Anthropic
    requires for cache_control, and the marker is present."""
    blocks = _system_prompt_blocks()
    assert isinstance(blocks, list)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == SYSTEM_PROMPT
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}


def test_tools_with_cache_marker_only_marks_last_tool():
    """All tool schemas preserved; only the LAST tool carries cache_control.

    Marking every tool would burn cache breakpoints (capped at 4 per request)
    without helping — the single trailing marker caches the whole tools array.
    """
    cached = _tools_with_cache_marker()
    assert len(cached) == len(TOOL_SCHEMAS)

    # First N-1 tools untouched
    for original, marked in zip(TOOL_SCHEMAS[:-1], cached[:-1], strict=True):
        assert marked is original or marked == original, "non-last tools must not be mutated"
        assert "cache_control" not in marked

    # Last tool carries the marker
    last = cached[-1]
    assert last["cache_control"] == {"type": "ephemeral"}
    # Last tool retains its original name/description/schema fields
    original_last = TOOL_SCHEMAS[-1]
    assert last["name"] == original_last["name"]
    assert last["input_schema"] == original_last["input_schema"]


def test_tools_with_cache_marker_does_not_mutate_global():
    """`_tools_with_cache_marker` must return a fresh list; the global
    TOOL_SCHEMAS object stays unmodified across calls (otherwise a second
    planner call would see the marker doubled or attached to wrong tool)."""
    _tools_with_cache_marker()
    _tools_with_cache_marker()
    # Original last tool never grew a cache_control key
    assert "cache_control" not in TOOL_SCHEMAS[-1]


def test_planner_passes_cache_marked_system_and_tools_to_anthropic():
    """End-to-end: the kwargs sent to `client.messages.create` carry the
    cached system block + cache-marked tools. Regression guard against
    someone reverting the wiring."""
    client = MagicMock()
    client.messages.create.return_value = _response_with_usage(
        _text_block("ok"), stop_reason="end_turn"
    )

    run_planner("ahoj", client=client)

    client.messages.create.assert_called_once()
    kwargs = client.messages.create.call_args.kwargs

    # System: list-of-blocks with cache_control
    assert isinstance(kwargs["system"], list)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}

    # Tools: cache_control on the last entry only
    assert isinstance(kwargs["tools"], list)
    assert kwargs["tools"][-1].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in kwargs["tools"][0]


# ---------------------------------------------------------------------------
# Token telemetry
# ---------------------------------------------------------------------------


def test_planner_emits_usage_log_per_iteration():
    """Every successful messages.create call gets a `planner_iteration_usage`
    log entry with the four standard fields.

    Uses `structlog.testing.capture_logs` because pytest's `caplog` only sees
    stdlib-logging records, and our structlog configuration writes through
    a different pipeline depending on whether `configure_logging` has been
    called. `capture_logs` is the structlog-native way to assert on events.
    """
    client = MagicMock()
    client.messages.create.return_value = _response_with_usage(
        _text_block("done"),
        stop_reason="end_turn",
        input_tokens=3000,
        output_tokens=42,
        cache_read_input_tokens=2500,
        cache_creation_input_tokens=0,
    )

    with structlog.testing.capture_logs() as logs:
        run_planner("test", client=client)

    usage_events = [e for e in logs if e.get("event") == "planner_iteration_usage"]
    assert len(usage_events) == 1, (
        f"Expected exactly one usage event per iteration, got {len(usage_events)}. "
        "Check that _log_usage() is wired into the loop."
    )
    event = usage_events[0]
    assert event["iteration"] == 0
    assert event["input_tokens"] == 3000
    assert event["output_tokens"] == 42
    assert event["cache_read_input_tokens"] == 2500
    assert event["cache_creation_input_tokens"] == 0


def test_planner_tolerates_missing_usage_field():
    """Existing tests in test_planner.py use SimpleNamespace responses without
    a `usage` attribute. _log_usage must default the four fields to 0 so
    those tests keep passing without modification."""
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[_text_block("hi")], stop_reason="end_turn"
    )

    with structlog.testing.capture_logs() as logs:
        run_planner("test", client=client)

    usage_events = [e for e in logs if e.get("event") == "planner_iteration_usage"]
    assert len(usage_events) == 1
    event = usage_events[0]
    # All four fields default to zero; no AttributeError raised.
    assert event["input_tokens"] == 0
    assert event["output_tokens"] == 0
    assert event["cache_read_input_tokens"] == 0
    assert event["cache_creation_input_tokens"] == 0


def test_planner_emits_usage_log_per_iteration_on_multi_turn(monkeypatch):
    """Multi-iteration flow: each tool_use roundtrip gets its own usage event.

    Validates that the telemetry fires N times for N iterations (so we can
    later sum cost across iterations of a long flow).
    """
    first = _response_with_usage(
        SimpleNamespace(type="tool_use", id="tu_a", name="show_version", input={}),
        stop_reason="tool_use",
        input_tokens=2000,
        output_tokens=20,
    )
    second = _response_with_usage(
        _text_block("IOS XE 17.6."),
        stop_reason="end_turn",
        input_tokens=200,
        output_tokens=15,
        cache_read_input_tokens=1800,
    )
    client = MagicMock()
    client.messages.create.side_effect = [first, second]

    from backend.orchestration import tool_registry

    monkeypatch.setitem(tool_registry._TOOL_FUNCS, "show_version", lambda: {"version": "17.6"})

    with structlog.testing.capture_logs() as logs:
        run_planner("show me the version", client=client)

    usage_events = [e for e in logs if e.get("event") == "planner_iteration_usage"]
    assert len(usage_events) == 2, (
        f"Expected 2 usage events across 2 iterations, got {len(usage_events)}"
    )
    # First iteration: full input, no cache.
    assert usage_events[0]["iteration"] == 0
    assert usage_events[0]["input_tokens"] == 2000
    assert usage_events[0]["cache_read_input_tokens"] == 0
    # Second iteration: most of the prefix cached — the cost-saving signal.
    assert usage_events[1]["iteration"] == 1
    assert usage_events[1]["input_tokens"] == 200
    assert usage_events[1]["cache_read_input_tokens"] == 1800
