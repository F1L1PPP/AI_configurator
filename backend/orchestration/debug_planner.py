"""Inner Haiku planner for diagnostic plans.

Two modes:
- draft_debug_plan(failure_context) — focused diagnosis given a failed
  action's error context (verify_command, verify_pattern, output preview,
  device_errors). Returns a short list of `show` commands tailored to
  prove or refute the most likely failure causes.
- draft_debug_sweep() — broad sweep when no specific failure (operator
  asked "diagnose router state"). Returns a curated `show` block.

Both return {commands, summary_intent, risk} for the propose layer to wrap.
Plus draft_debug_summary(outputs, failure_context) — runs after the sweep
executes, takes the show outputs and produces a plain-English digest.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic

from backend.core.logging import get_logger
from backend.core.settings import get_settings
from backend.orchestration.json_extract import extract_first_json_object

log = get_logger(__name__)

_PLANNER_MODEL = "claude-haiku-4-5-20251001"
_PLANNER_MAX_TOKENS = 1024

_PLAN_SYSTEM_PROMPT = """\
You are diagnosing why a Cisco IOS XE config write returned verify_failed.

You receive a failure context dict containing:
- verify_command: the show command that was run after the config push
- verify_pattern: the Python regex that did NOT match
- verify_output_preview: first 3000 chars of the verify command output
- device_errors: list of '%' error lines from the config push (may be empty)

Your job: propose 1-3 short `show` commands that will distinguish between:
  1. Config landed but the verify pattern was too strict (regex mismatch)
  2. Config didn't land (IOS XE silently discarded it)
  3. Device rejected the config (router reported an error, config in bad state)

For common failure types, prefer these targeted shows:
  - Static route missing: `show ip route static | include <prefix>` proves landing
  - Interface IP: `show running-config interface <iface>` shows actual config
  - VLAN not present: `show vlan brief` shows all VLANs
  - OSPF process: `show ip ospf | include Routing Process`
  - Hostname: `show running-config | include hostname`
  - ACL: `show ip access-lists <name>`
  - BGP: `show ip bgp summary`

Output a JSON object with this exact shape:
{
  "commands": ["show ...", "show ..."],
  "summary_intent": "one sentence: what these commands will prove or refute",
  "risk": "low — read-only show commands"
}

Rules:
1. Output JSON only — no prose, no Markdown fences.
2. Every command in `commands` must start with `show `. Never include configure,
   reload, erase, or any write command.
3. Prefer `| include` over `| section` — line-grep is reliable; section-grep
   fails silently on many IOS XE features.
4. `commands` may be an empty list [] if the failure context is incoherent or
   you cannot propose useful targeted shows. The operator will see a
   "couldn't diagnose" message — that is acceptable.
5. Do NOT speculate in chat text. Return only the JSON.
"""

_SWEEP_SYSTEM_PROMPT = """\
You are performing a broad health check on a Cisco IOS XE router. The operator
asked to "diagnose router state" without a specific failure to investigate.

Output a JSON object with exactly this shape:
{
  "commands": ["show ...", "show ...", "show ...", "show ...", "show ..."],
  "summary_intent": "Broad health sweep: interfaces, routing, logs, recent changes",
  "risk": "low — read-only show commands"
}

Rules:
1. Output JSON only — no prose, no Markdown fences.
2. Every command in `commands` must start with `show `.
3. Include 4-6 commands covering:
   - Interface state: `show ip interface brief`
   - Routing table summary: `show ip route summary`
   - Recent syslog: `show logging | tail 20`
   - Running-config changes: `show running-config | include hostname`
   - CPU/memory: `show processes cpu sorted | head 10`
   - A second diagnostic of your choice relevant to a C1111 router
4. Prefer `| include` and `| tail` filters over raw full outputs.
5. Do NOT include configure, reload, erase, or any write command.
"""

_SUMMARY_SYSTEM_PROMPT = """\
You synthesize diagnostic show-command outputs into a plain-English digest for a
Cisco network engineer. You receive:
- raw outputs from one or more `show` commands (command → output text)
- optionally the original failure context (verify_command, verify_pattern,
  verify_output_preview, device_errors) if this was a reactive diagnosis

Your job: write a 3-5 sentence plain-English digest.

If failure_context is provided (reactive mode):
  - State clearly whether the configuration appears to have landed on the device
    (cite specific output lines as evidence)
  - Explain what likely went wrong: pattern too strict, config silently rejected,
    device error, or another cause
  - Recommend the most likely next action (e.g. "adjust verify_pattern", "check
    device errors", "re-apply with corrected syntax")

If failure_context is absent (broad sweep mode):
  - Summarize router health: interface states, routing status, recent log anomalies
  - Flag any anomalies you observe (interfaces down, unexpected routes, error logs)

Output plain text only — no JSON, no Markdown headers. Be concise and direct.
"""


def _make_client() -> Anthropic:
    return Anthropic(api_key=get_settings().anthropic_api_key, max_retries=5)


def _parse_json_result(text: str, context: str) -> dict[str, Any]:
    """Try json.loads; fall back to brace-balanced extractor on prose wrapping."""
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        extracted = extract_first_json_object(text)
        if extracted is None:
            log.warning(
                "debug_planner_json_parse_failed",
                context=context,
                text_preview=text[:200],
            )
            return {
                "commands": [],
                "summary_intent": "Could not parse diagnostic plan from LLM",
                "risk": "low — read-only show commands",
            }
        try:
            result = json.loads(extracted)
            log.warning(
                "debug_planner_recovered_from_prose",
                context=context,
                prose_len=len(text),
                json_len=len(extracted),
            )
        except json.JSONDecodeError:
            log.warning(
                "debug_planner_json_parse_failed_after_extract",
                context=context,
                text_preview=text[:200],
            )
            return {
                "commands": [],
                "summary_intent": "Could not parse diagnostic plan from LLM",
                "risk": "low — read-only show commands",
            }
    if not isinstance(result, dict):
        return {
            "commands": [],
            "summary_intent": "LLM output was not a JSON object",
            "risk": "low — read-only show commands",
        }
    return result


def draft_debug_plan(
    failure_context: dict[str, Any], client: Anthropic | None = None
) -> dict[str, Any]:
    """Draft a focused diagnostic show plan for a specific verify_failed action.

    Returns ``{commands, summary_intent, risk}``. On OverloadedError or any
    exception, returns a safe fallback with empty commands (does NOT raise).
    """
    if client is None:
        client = _make_client()

    user_msg = f"Failure context:\n{json.dumps(failure_context, indent=2, default=str)}"

    try:
        response = client.messages.create(
            model=_PLANNER_MODEL,
            max_tokens=_PLANNER_MAX_TOKENS,
            system=_PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        log.error("draft_debug_plan_llm_failed", error=str(exc), exc_type=type(exc).__name__)
        return {
            "commands": [],
            "summary_intent": "LLM overloaded or unavailable — check `show running-config` and `show logging` manually",
            "risk": "low — read-only show commands",
        }

    text = "\n".join(
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    ).strip()

    result = _parse_json_result(text, context="draft_debug_plan")
    return {
        "commands": result.get("commands") or [],
        "summary_intent": result.get("summary_intent", "Focused diagnostic plan"),
        "risk": result.get("risk", "low — read-only show commands"),
    }


def draft_debug_sweep(client: Anthropic | None = None) -> dict[str, Any]:
    """Draft a broad health-check show plan (no specific failure context).

    Returns ``{commands, summary_intent, risk}``. On OverloadedError or any
    exception, returns a safe fallback with empty commands (does NOT raise).
    """
    if client is None:
        client = _make_client()

    try:
        response = client.messages.create(
            model=_PLANNER_MODEL,
            max_tokens=_PLANNER_MAX_TOKENS,
            system=_SWEEP_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": "Draft a broad health-check show plan for a Cisco IOS XE C1111 router.",
                }
            ],
        )
    except Exception as exc:
        log.error("draft_debug_sweep_llm_failed", error=str(exc), exc_type=type(exc).__name__)
        return {
            "commands": [],
            "summary_intent": "LLM overloaded or unavailable — check `show ip interface brief` and `show logging` manually",
            "risk": "low — read-only show commands",
        }

    text = "\n".join(
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    ).strip()

    result = _parse_json_result(text, context="draft_debug_sweep")
    return {
        "commands": result.get("commands") or [],
        "summary_intent": result.get("summary_intent", "Broad health sweep"),
        "risk": result.get("risk", "low — read-only show commands"),
    }


def draft_debug_summary(
    outputs: dict[str, str],
    failure_context: dict[str, Any] | None = None,
    client: Anthropic | None = None,
) -> str:
    """Synthesize show outputs into a plain-English digest via Haiku.

    Args:
        outputs: mapping of show command → raw output text
        failure_context: the original failure dict if reactive, else None

    Returns plain text digest string. On any exception, returns a static
    fallback message (does NOT raise).
    """
    if client is None:
        client = _make_client()

    outputs_blob = "\n\n".join(f"=== {cmd} ===\n{output}" for cmd, output in outputs.items())
    if failure_context:
        user_msg = (
            f"Original failure context:\n{json.dumps(failure_context, indent=2, default=str)}"
            f"\n\nDiagnostic show outputs:\n{outputs_blob}"
        )
    else:
        user_msg = f"Diagnostic show outputs:\n{outputs_blob}"

    try:
        response = client.messages.create(
            model=_PLANNER_MODEL,
            max_tokens=_PLANNER_MAX_TOKENS,
            system=_SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        log.error("draft_debug_summary_llm_failed", error=str(exc), exc_type=type(exc).__name__)
        return "Couldn't draft digest — check `show running-config` and `show logging` manually."

    text = "\n".join(
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    ).strip()

    return text or "Diagnostic outputs collected but no summary was produced."
