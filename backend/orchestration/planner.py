"""LLM-driven planner — Anthropic tool-use loop.

Translates natural-language requests into tool calls against the Cisco C1111.
Claude (Haiku 4.5) picks the tool and extracts parameters; this module
executes the picked tool deterministically and feeds results back to the
model until it produces a final text answer.

Model choice: Haiku 4.5 is the right size for this — 8 well-defined tools,
short structured outputs, no deep reasoning required. ~2× faster and ~5×
cheaper than Sonnet 4.6 for the same accuracy on this workload. Swap to
Sonnet via the MODEL constant if you ever see quality regressions.

Safety:
- Read tools execute immediately.
- Write tools are always two-step: Claude calls `propose_*` first, which
  registers an action_id in PROPOSED state. The human must approve it via
  POST /api/approve/{action_id} before Claude calls the matching execute
  tool. The execute tool itself also re-checks `is_approved()` server-side.
- Hard cap on tool-use iterations to prevent runaway loops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from anthropic import Anthropic

from backend.core.eventbus import bus
from backend.core.logging import get_logger
from backend.core.settings import get_settings
from backend.orchestration.tool_registry import TOOL_SCHEMAS, WRITE_TOOLS, execute_tool

log = get_logger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096
MAX_ITERATIONS = 8


SYSTEM_PROMPT = """\
You are a Cisco network configuration assistant for a single Cisco C1111 \
router. Speak Slovak by default; switch to English if the user writes in \
English or asks for it.

## Tools you have

Read (safe to call anytime):
- show_version, show_ip_interface_brief, show_running_config, show_vlan_brief
- search_docs — semantic search over the curated Cisco C1111 / IOS XE 17.x doc corpus

Write — CLI path (fast, no browser):
- propose_set_hostname -> set_hostname
- propose_set_interface_ip -> set_interface_ip

Write — WebUI path (slower, opens a Chromium window the user can watch):
- propose_webui_set_hostname -> webui_set_hostname

Both write paths are two-step: always propose first, wait for human approval.

## Hard rules

1. Never call set_hostname, set_interface_ip, or webui_set_hostname
   directly. Always call the matching propose_* tool first. The propose_*
   tool returns an action_id; stop and tell the user to approve it in the
   Preview screen.

2. **When the user references an action_id (looks like `act_*`) and says
   things like "vykonaj", "execute", "schválená", "approved, run it":**
   - DO NOT propose a new action. The user is talking about an EXISTING
     action that you proposed earlier in this same conversation.
   - Look at your prior tool_use in the conversation history for that
     action_id. The propose_* tool's `tool_result` includes an
     `execute_tool` field — that's the tool you must call now.
   - Call that tool with the SAME parameters from the original propose
     call, plus `action_id` = the one the user mentioned.
   - Examples of the propose → execute mapping (the `execute_tool` field
     in the propose response tells you which one for any given action):
     - propose_set_hostname        → set_hostname
     - propose_set_interface_ip    → set_interface_ip
     - propose_webui_set_hostname  → webui_set_hostname
   - NEVER swap CLI for WebUI (or vice versa) during execution. If the
     user originally asked for the WebUI path, execute via webui_set_*.

3. Choosing CLI vs WebUI when the user first asks for a change:
   - If the user says "via WebUI", "via UI", "v prehliadači", "cez WebUI",
     "ukáž mi ako", "demo" — use propose_webui_set_hostname.
   - Otherwise default to propose_set_hostname (CLI is faster and more
     reliable; WebUI is for demos and visual verification).

4. Never invent device data. If the user asks something you don't know,
   call a read tool first. For ANY configuration question (CLI syntax,
   WebUI nav, defaults, supported features), call `search_docs` with a
   focused query FIRST to ground your answer in the actual Cisco docs —
   then summarize. When you used `search_docs` results, end your reply
   with a short **Sources** section listing each cited document and
   section, e.g.:
       **Sources**
       - isr1100-sw-config.pdf — Basic Router Configuration
   Skip the Sources section only if you did not call `search_docs` in
   this turn.

5. Stay in scope: hostname changes (CLI or WebUI), interface IP
   assignments, VLAN add (Day 7), and read operations. If asked for
   OSPF/ACL/DHCP/static routes/anything else, politely refuse and explain
   what's in scope.

6. One C1111 only — no multi-device targeting.

7. If a tool returns an error, surface it to the user clearly. Never retry
   a write operation automatically.

## Response style

Concise. Use Markdown sparingly. When you've shown the user data from a
read tool, summarize the key fact (e.g. "Vlan1 is up at 192.168.10.1") \
rather than dumping the raw output."""


@dataclass
class PlannerEvent:
    """One event in the planner's execution trace."""

    kind: str  # agent_thinking | tool_call | tool_result | awaiting_approval | applied | verified | error
    data: dict[str, Any] = field(default_factory=dict)


def _emit(events: list[PlannerEvent], kind: str, data: dict[str, Any]) -> None:
    """Append to the in-memory trace AND publish to the live event bus."""
    events.append(PlannerEvent(kind, data))
    bus.publish(
        {
            "type": kind,
            "ts": datetime.now(UTC).isoformat(),
            "data": data,
        }
    )


@dataclass
class PlannerResult:
    final_text: str
    events: list[PlannerEvent]
    messages: list[dict[str, Any]]  # full conversation, for follow-up turns
    stop_reason: str = "end_turn"


def _text_from_response(response: Any) -> str:
    """Concatenate all text blocks in an Anthropic Message response."""
    # Single-pass generator + join — no manual loop / list growth.
    return "\n".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()


def _serialize_assistant_content(response: Any) -> list[dict]:
    """Convert response.content (list of typed blocks) into wire-format dicts.

    Anthropic SDK returns ContentBlock objects; for follow-up turns we need
    the dict form to append back to messages.

    Known block types (text, tool_use) get an explicit shape. Unknown blocks
    (e.g. future `thinking`/`server_tool_use`/`web_search_tool_result`) are
    preserved via the Pydantic `model_dump()` fallback so follow-up turns
    don't lose context and debugging stays readable.
    """
    out: list[dict] = []
    for block in response.content:
        kind = getattr(block, "type", None)
        if kind == "text":
            out.append({"type": "text", "text": block.text})
        elif kind == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        else:
            # Future-proof: preserve unknown block via the SDK's Pydantic
            # serializer. Log so we notice new block types showing up.
            try:
                out.append(block.model_dump(mode="json", exclude_none=True))
                log.info("assistant_block_preserved", kind=kind)
            except Exception as exc:
                log.warning("assistant_block_dropped", kind=kind, error=str(exc))
    return out


def run_planner(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    client: Anthropic | None = None,
) -> PlannerResult:
    """Run one turn of the planner.

    Args:
        user_message: The new user message to append to the conversation.
        history: Previous messages from earlier turns (assistant + tool_result
            blocks). Pass None on the first turn.
        client: Inject an Anthropic client for tests. Defaults to a fresh
            client built from settings.

    Returns a PlannerResult with the final text, the event trace, and the
    full message history to pass to the next turn.
    """
    settings = get_settings()
    if client is None:
        client = Anthropic(api_key=settings.anthropic_api_key)

    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": user_message})

    events: list[PlannerEvent] = []

    for iteration in range(MAX_ITERATIONS):
        _emit(events, "agent_thinking", {"iteration": iteration, "model": MODEL})
        log.info("planner_iteration", iteration=iteration)

        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        stop_reason = response.stop_reason

        # Always append the assistant's full content (text + tool_use blocks)
        assistant_content = _serialize_assistant_content(response)
        messages.append({"role": "assistant", "content": assistant_content})

        if stop_reason != "tool_use":
            # end_turn, max_tokens, stop_sequence, etc.
            final_text = _text_from_response(response)
            log.info("planner_done", stop_reason=stop_reason, iterations=iteration + 1)
            return PlannerResult(
                final_text=final_text,
                events=events,
                messages=messages,
                stop_reason=stop_reason,
            )

        # Tool use — execute each tool_use block and append tool_results
        tool_result_blocks: list[dict] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue

            _emit(
                events,
                "tool_call",
                {"name": block.name, "input": dict(block.input), "id": block.id},
            )
            log.info("tool_call", tool=block.name, params=block.input)

            result = execute_tool(block.name, dict(block.input))
            _emit(events, "tool_result", {"name": block.name, "result": result})

            # Surface action proposals as a dedicated event for UI consumption
            if isinstance(result, dict) and result.get("status") == "awaiting_approval":
                _emit(
                    events,
                    "awaiting_approval",
                    {
                        "action_id": result.get("action_id"),
                        "preview": result.get("preview"),
                    },
                )

            # Successful write → emit `applied` so the UI can advance the timeline.
            # We infer success from: no `error` key in result + a `snapshot_post`
            # path (write tools always set this on success).
            if (
                block.name in WRITE_TOOLS
                and isinstance(result, dict)
                and "error" not in result
                and result.get("snapshot_post")
            ):
                _emit(
                    events,
                    "applied",
                    {
                        "tool": block.name,
                        "summary": result.get("output", "")[:200],
                        "snapshot_post": result.get("snapshot_post"),
                        "duration_ms": result.get("duration_ms"),
                    },
                )

            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )

        messages.append({"role": "user", "content": tool_result_blocks})

    # Hit iteration cap
    _emit(events, "error", {"message": "iteration_cap_reached", "max": MAX_ITERATIONS})
    log.warning("planner_iteration_cap", iterations=MAX_ITERATIONS)
    return PlannerResult(
        final_text="(stopped: too many tool-use iterations)",
        events=events,
        messages=messages,
        stop_reason="iteration_cap",
    )
