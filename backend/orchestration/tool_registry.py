"""Tool schemas in Anthropic format + dispatcher.

The planner exposes these tools to Claude. The dispatcher maps each tool
name to a Python callable, runs it, and returns a JSON-serializable result.

Design notes:
- Read tools take no parameters and never need approval.
- Write tools listed in `_REQUIRES_APPROVAL` MUST carry an `action_id` that
  is already in state APPROVED. The dispatcher verifies this BEFORE calling
  the underlying function (defense-in-depth layer 1). The write tool itself
  also re-checks `is_approved()` server-side (layer 2) so neither layer
  alone is the only gate.
- Unknown tool names return a structured error instead of raising; this lets
  the planner recover instead of dying mid-conversation.
"""

from __future__ import annotations

import contextlib
import inspect
import re
from collections.abc import Callable
from typing import Any

from anthropic._exceptions import OverloadedError as AnthropicOverloadedError

from backend.cli_agent import read_tools, write_tools
from backend.cli_agent.write_tools import (
    _validate_config_commands,
    _validate_hostname,
    _validate_interface,
    _validate_interface_ip_and_mask,
    _validate_verify_command,
    _validate_verify_pattern,
    _validate_vlan_id,
    _validate_vlan_name,
)
from backend.core.logging import get_logger
from backend.orchestration.cli_configure_planner import draft_cli_plan
from backend.orchestration.configure_planner import draft_atlas_plan
from backend.orchestration.confirmations import (
    NotApproved,
    is_approved,
    propose_action,
)
from backend.orchestration.conflict_detector import find_existing_block
from backend.webui_agent.flows.add_access_vlan import add_access_vlan_via_webui
from backend.webui_agent.flows.change_hostname import change_hostname_via_webui
from backend.webui_agent.generic_driver import (
    close_all_sessions,
    webui_act_field,
    webui_apply_control,
    webui_open,
    webui_open_form_for_planning,
    webui_perceive,
    webui_reload_for_planning,
    webui_verify_a11y,
)

# Maximum length of a search_docs query. Caps the embedding cost — a 10 MB
# query embedded through MiniLM is several seconds of CPU per call, easy
# DoS surface if the planner ever produces (or is tricked into producing)
# a runaway string.
_SEARCH_DOCS_MAX_QUERY_CHARS = 1000
_SEARCH_DOCS_MAX_TOP_K = 50

# Repeated next_step copy for every propose_* helper. One source of truth
# so a UX wording change doesn't require five edits.
_NEXT_STEP_INLINE = "Use the APPROVE and EXECUTE NOW buttons below this message."
_NEXT_STEP_WEBUI = (
    _NEXT_STEP_INLINE + " Headed Chromium will open when you click EXECUTE NOW so you "
    "can watch the clicks."
)

# Button/link names (lowercased, stripped) that indicate a Cisco WebUI list-page
# "open form" trigger.  When the describe view contains one of these and has NO
# fillable textboxes, the atlas propose path opens the form at propose-time so
# the authoritative draft_atlas_plan call sees the real field names rather than just
# the Add button label.
# "add process" is included because the OSPF planner prompt names it explicitly
# as a valid single-step form-open click.
_FORM_TRIGGER_NAMES_LOWER: frozenset[str] = frozenset(
    {"add", "create", "new", "+", "add dhcp pool", "add process"}
)

# Buttons/links whose presence means the form is ALREADY open (e.g. "Apply to
# Device", "Save", "Submit").  Used by the open-form heuristic: if a submit
# button is visible, the form is already rendered — skip the form-open step.
_FORM_SUBMIT_NAMES_LOWER: frozenset[str] = frozenset(
    {"apply", "apply to device", "save", "submit", "ok", "commit"}
)


def _search_docs(**kwargs: Any) -> dict:
    """Lazy wrapper around `knowledge_agent.retrieve.search_docs`.

    Importing `retrieve` at module-load time would pull in `chromadb` and
    `sentence_transformers` (and transitively `torch`) for every consumer
    of the registry — including workers that never call search_docs. Defer
    the import until the tool actually runs; Python caches the import in
    `sys.modules` so only the first call pays the cost.

    Pluck named params explicitly rather than `**kwargs` → `**kwargs` so
    an extra key from the planner doesn't TypeError on the inner call and
    so query/top_k get hard-validated here (length cap, type check).
    """
    query = kwargs.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "bad_parameters", "message": "query must be a non-empty string"}
    if len(query) > _SEARCH_DOCS_MAX_QUERY_CHARS:
        return {
            "error": "bad_parameters",
            "message": (f"query too long ({len(query)} chars; max {_SEARCH_DOCS_MAX_QUERY_CHARS})"),
        }
    top_k = kwargs.get("top_k", 5)
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        return {"error": "bad_parameters", "message": "top_k must be an integer"}
    if not (1 <= top_k <= _SEARCH_DOCS_MAX_TOP_K):
        return {
            "error": "bad_parameters",
            "message": f"top_k must be between 1 and {_SEARCH_DOCS_MAX_TOP_K}",
        }

    from backend.knowledge_agent import retrieve as kb_retrieve

    return kb_retrieve.search_docs(query=query, top_k=top_k)


log = get_logger(__name__)

# Tools in this set require an APPROVED action_id before the dispatcher
# will invoke them. Mirrors the gate inside each write tool.
# Re-exported as the canonical "this tool writes to the router" set —
# the planner imports it to decide when to emit `applied` events.
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "set_hostname",
        "set_interface_ip",
        "set_access_vlan",
        "webui_set_hostname",
        "webui_add_access_vlan",
        # Phase 5 — generic AI-driven WebUI configure. The outer LLM's only
        # WebUI write path is now propose_webui_configure → APPROVE →
        # webui_configure. webui_act / webui_act_by_intent are internal
        # helpers only (not in TOOL_SCHEMAS).
        "webui_configure",
        # CLI AI configure — Haiku drafts IOS XE commands, denylist + human
        # approval gate, Netmiko applies, regex-verifies post-state.
        "cli_configure",
        # Chunk 12 — diagnostic sweep executor. Read-only (show commands only),
        # but gated behind the same two-click Approve+Execute contract so the
        # operator sees and controls every sweep. propose_debug_sweep →
        # APPROVE → debug_sweep.
        "debug_sweep",
    }
)
_REQUIRES_APPROVAL = WRITE_TOOLS


# ---------------------------------------------------------------------------
# Anthropic tool schemas
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "show_version",
        "description": (
            "Run 'show version' on the Cisco C1111 and return parsed hardware "
            "and software info (IOS XE version, uptime, serial, model). "
            "Read-only — safe to call any time."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "show_ip_interface_brief",
        "description": (
            "Run 'show ip interface brief' on the Cisco C1111 and return a list "
            "of interfaces with IP, status, and protocol state. Read-only."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "show_running_config",
        "description": (
            "Run 'show running-config' and return the full running configuration "
            "as a single string. Read-only; output can be large (~6 kB)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "show_vlan_brief",
        "description": (
            "Run 'show vlan brief' and return a list of VLANs with name, status, "
            "and assigned ports. Read-only."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_docs",
        "description": (
            "Semantic search over the curated Cisco C1111 / IOS XE 17.x documentation "
            "corpus. Returns up to top_k chunks, each with source filename, section "
            "heading, and a relevance score. Call this BEFORE generating CLI commands "
            "or WebUI steps for any topic you're not certain about — it grounds your "
            "answer in real Cisco docs. Read-only.\n\n"
            "Cost tip: prefer `top_k=3` for narrow lookups (specific feature like "
            "'how to create OSPF route via WebUI'). Use `top_k=5` only for broader "
            "explanatory questions ('explain VLAN trunking'). Each extra chunk is "
            "~250 tokens added to the context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query, e.g. 'how to change hostname on ISR 1100'.",
                },
                "top_k": {
                    "type": "integer",
                    "description": (
                        "Max number of chunks to return. Default 5. "
                        "Use 3 for narrow / specific-feature lookups to keep cost "
                        "down; 5 for broader topics."
                    ),
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "propose_set_hostname",
        "description": (
            "Propose a hostname change. Does NOT touch the router — only "
            "registers the action and returns an action_id. The human must then "
            "approve it via the Preview screen before set_hostname will execute. "
            "Always call this first for any hostname change request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_name": {
                    "type": "string",
                    "description": "The new hostname (e.g. 'LAB-R1').",
                },
            },
            "required": ["new_name"],
        },
    },
    {
        "name": "set_hostname",
        "description": (
            "Execute a previously approved hostname change. Requires an "
            "action_id that has been approved (state == APPROVED). Pre/post "
            "snapshots are captured automatically. Never call without first "
            "proposing and waiting for approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_name": {"type": "string"},
                "action_id": {"type": "string"},
            },
            "required": ["new_name", "action_id"],
        },
    },
    {
        "name": "propose_set_interface_ip",
        "description": (
            "Propose an interface IP assignment. Does NOT touch the router — "
            "returns an action_id that must be approved before execution."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": "Full interface name, e.g. 'GigabitEthernet0/0/0'.",
                },
                "ip": {"type": "string", "description": "IPv4 address."},
                "mask": {"type": "string", "description": "Subnet mask (dotted)."},
            },
            "required": ["interface", "ip", "mask"],
        },
    },
    {
        "name": "set_interface_ip",
        "description": (
            "Execute a previously approved interface IP assignment. Requires an APPROVED action_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string"},
                "ip": {"type": "string"},
                "mask": {"type": "string"},
                "action_id": {"type": "string"},
            },
            "required": ["interface", "ip", "mask", "action_id"],
        },
    },
    {
        "name": "propose_webui_set_hostname",
        "description": (
            "Propose a hostname change executed via the Cisco WebUI "
            "(Playwright drives the browser through Administration → Device "
            "Properties → Hostname → Apply). Does NOT touch the router — "
            "returns an action_id that must be approved before the matching "
            "webui_set_hostname runs. Prefer this over propose_set_hostname "
            "when the user wants to *see* the WebUI being driven (e.g. demo)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_name": {
                    "type": "string",
                    "description": "The new hostname (e.g. 'LAB-R1').",
                },
            },
            "required": ["new_name"],
        },
    },
    {
        "name": "webui_set_hostname",
        "description": (
            "Execute a previously approved WebUI hostname change. Launches "
            "headed Chromium, logs in, navigates the form, fills it, clicks "
            "Apply, screenshots every step into artifacts/screenshots/, then "
            "verifies via CLI 'show running-config | i hostname'. Requires "
            "an APPROVED action_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_name": {"type": "string"},
                "action_id": {"type": "string"},
            },
            "required": ["new_name", "action_id"],
        },
    },
    {
        "name": "propose_set_access_vlan",
        "description": (
            "Propose a CLI access-VLAN add. Does NOT touch the router — "
            "registers the action and returns an action_id that must be "
            "approved before set_access_vlan executes. Use this when the "
            "user wants the fast CLI path (no browser); for visible "
            "screenshot evidence, prefer propose_webui_add_access_vlan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vlan_id": {
                    "type": "integer",
                    "description": "VLAN number (1–4094).",
                },
                "vlan_name": {
                    "type": "string",
                    "description": "Human-readable VLAN name (1–32 chars, letters/digits/_/-).",
                },
            },
            "required": ["vlan_id", "vlan_name"],
        },
    },
    {
        "name": "set_access_vlan",
        "description": (
            "Execute a previously approved CLI access-VLAN add. Runs "
            "'vlan <id>' + 'name <name>' inside config mode via SSH, "
            "takes pre/post snapshots, and returns the raw output. "
            "Requires an APPROVED action_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vlan_id": {"type": "integer"},
                "vlan_name": {"type": "string"},
                "action_id": {"type": "string"},
            },
            "required": ["vlan_id", "vlan_name", "action_id"],
        },
    },
    {
        "name": "propose_webui_add_access_vlan",
        "description": (
            "Propose an access VLAN add executed via the Cisco WebUI "
            "(Playwright drives Configuration → Layer 2 → VLAN → Add → "
            "fill ID + Name → Save). Does NOT touch the router — returns "
            "an action_id that must be approved before webui_add_access_vlan "
            "runs. This is the preferred path for VLAN add: it produces "
            "screenshot evidence the demo evaluator can verify directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vlan_id": {
                    "type": "integer",
                    "description": "VLAN number (1–4094).",
                },
                "vlan_name": {
                    "type": "string",
                    "description": "Human-readable VLAN name (e.g. 'OFFICE').",
                },
            },
            "required": ["vlan_id", "vlan_name"],
        },
    },
    {
        "name": "webui_add_access_vlan",
        "description": (
            "Execute a previously approved WebUI access-VLAN add. Launches "
            "headed Chromium, logs in, navigates Configuration → Layer 2 → "
            "VLAN, clicks Add, fills VLAN ID + Name, clicks Save, "
            "screenshots every step into artifacts/screenshots/, then "
            "verifies via CLI 'show vlan brief' that the row is present. "
            "Requires an APPROVED action_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vlan_id": {"type": "integer"},
                "vlan_name": {"type": "string"},
                "action_id": {"type": "string"},
            },
            "required": ["vlan_id", "vlan_name", "action_id"],
        },
    },
    {
        "name": "propose_webui_configure",
        "description": (
            "Propose a generic WebUI configuration based on a natural-language intent. "
            "Use this for anything beyond the fast-path tools (hostname / interface IP / "
            "access VLAN add): OSPF, RIP, ACLs, DHCP, static routes, trunk VLANs, "
            "advanced interface settings, etc. The tool grounds the plan in the Cisco "
            "manual via search_docs and the current WebUI page via describe_page, then "
            "returns a step plan for human approval. Two-step: always call this first, "
            "then wait for APPROVE. Do NOT call webui_configure directly without a "
            "prior propose_webui_configure call from the same turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": (
                        "Natural-language description of what to configure, e.g. "
                        "'configure OSPF process 100 area 0 on GigabitEthernet0/0/1' "
                        "or 'add static route 10.0.0.0/24 via 192.168.1.1'."
                    ),
                },
                "webui_path": {
                    "type": "string",
                    "description": (
                        "WebUI hash route to open before drafting the plan, e.g. "
                        "'/webui/#/routing/ospf'. Derived from search_docs if known."
                    ),
                },
            },
            "required": ["intent", "webui_path"],
        },
    },
    {
        "name": "webui_configure",
        "description": (
            "Execute a previously-approved WebUI configuration plan. Requires an "
            "action_id from a propose_webui_configure call that has been APPROVED by "
            "the human. Runs each plan step via the internal act-by-intent + self-heal "
            "machinery, screenshots at every step, and verifies the success text if "
            "specified. Marks the action EXECUTED on success or FAILED on any step error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "Action ID from the matching propose_webui_configure call.",
                },
            },
            "required": ["action_id"],
        },
    },
    {
        "name": "propose_cli_configure",
        "description": (
            "Propose an AI-drafted IOS XE configuration over SSH. Use this for CLI "
            "configuration tasks that DON'T match a narrow tool (set_hostname, "
            "set_interface_ip, set_access_vlan) — e.g. OSPF, BGP, route-maps, ACLs, "
            "debug commands. The inner Haiku planner drafts a list of config-mode "
            "commands + a 'show' verify command + a regex pattern, grounded in RAG "
            "manual chunks and the current running-config. Returns an action_id; the "
            "human must APPROVE before cli_configure will run. Prefer the narrow "
            "tools when intent is unambiguous — they're cheaper and more strictly "
            "validated. Does NOT touch the router."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": (
                        "Natural-language description of the CLI configuration "
                        "task, e.g. 'Configure OSPF process 100 area 0 on Vlan1' or "
                        "'Add ACL 10 permitting only 192.168.10.0/24'."
                    ),
                },
            },
            "required": ["intent"],
        },
    },
    {
        "name": "cli_configure",
        "description": (
            "Execute a previously-approved CLI configuration plan. Requires an "
            "action_id from a propose_cli_configure call that has been APPROVED by "
            "the human. Server-side denylist validators re-run before any router "
            "contact. Pushes commands via Netmiko, runs the verify command in EXEC "
            "mode, regex-matches the verify pattern. Marks the action EXECUTED on "
            "verify match or FAILED on any error. No auto-rollback."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "Action ID from the matching propose_cli_configure call.",
                },
            },
            "required": ["action_id"],
        },
    },
    {
        "name": "propose_debug_sweep",
        "description": (
            "Propose a diagnostic show plan. Two modes — pick based on the user's "
            "most recent message:\n\n"
            "(1) REACTIVE: when the user message looks like 'Please diagnose "
            "action_id=act_XXX which failed at execute time: ...' (this is the "
            "frontend's auto-debug trigger), you MUST extract the action_id token "
            "(`act_YYYYMMDD_NNNNNN`) from the message and pass it as "
            "`failure_action_id=act_XXX`. Drafts 1-3 NARROWED `show` commands "
            "that re-query the specific failed change (e.g. for a failed `ip route` "
            "write, the plan should include `show ip route static | include <prefix>` "
            "— NOT a broad sweep). Failing to pass failure_action_id falls through "
            "to a broad sweep, which loses the focused diagnosis the operator needs.\n\n"
            "(2) ON-DEMAND: when the user explicitly asks for a broad sweep "
            "(e.g. 'diagnose router state', 'debug my config', 'what's wrong'), "
            "call with NO arguments. Drafts a broader 4-6 command health sweep.\n\n"
            "Returns awaiting_approval; operator APPROVE+EXECUTE. All commands "
            "are read-only `show` commands. After execution, returns a "
            "plain-English digest synthesized by Haiku."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "failure_action_id": {
                    "type": "string",
                    "description": (
                        "action_id of the failed action to diagnose, in the form "
                        "`act_YYYYMMDD_NNNNNN`. MUST be extracted from the user's "
                        "auto-debug message when one is present (e.g. message "
                        "'Please diagnose action_id=act_20260521_ed8207 which "
                        'failed...\' → pass `failure_action_id="act_20260521_ed8207"`). '
                        "Omit ONLY for genuinely broad on-demand sweeps with no "
                        "specific failure context."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "debug_sweep",
        "description": (
            "Execute a previously-approved diagnostic show plan. Requires an "
            "action_id from a propose_debug_sweep call that has been APPROVED by "
            "the human. Runs each show command via SSH, collects raw outputs, and "
            "asks Haiku to synthesize a plain-English digest. Read-only — never "
            "modifies the router."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "Action ID from the matching propose_debug_sweep call.",
                },
            },
            "required": ["action_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatch table — tool name → callable
# ---------------------------------------------------------------------------


def _detect_hostname_conflict(new_name: str) -> dict | None:
    """Shared propose-time conflict check for hostname changes.

    Called by both CLI (_propose_set_hostname) and WebUI
    (_propose_webui_set_hostname) fast-path tools so behaviour can't drift.
    Returns a `preview_meta` dict (with existing_entity / existing_block /
    is_exact_match) when a conflict is found, or None if SSH soft-fails or
    no existing hostname line matches the proposed one.
    """
    running_config = ""
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.warning("hostname_conflict_precheck_read_failed", error=str(exc))
    if not running_config:
        return None
    existing = find_existing_block([f"hostname {new_name}"], running_config)
    if not existing:
        return None
    return {
        "existing_entity": existing["anchor"],
        "existing_block": existing["block"],
        "is_exact_match": existing["is_exact_match"],
    }


def _detect_vlan_conflict(vlan_id: int, vlan_name: str) -> dict | None:
    """Shared propose-time conflict check for VLAN add/rename.

    Called by both CLI (_propose_set_access_vlan) and WebUI
    (_propose_webui_add_access_vlan) fast-path tools so the C1111-4P
    vlan.dat fallback applies to both. Returns a `preview_meta` dict or
    None. The fallback path queries `show vlan brief` when the universal
    detector misses the stanza in running-config — VLAN definitions on
    the embedded switch live in vlan.dat and don't always appear in
    `show running-config` as a clean `vlan N / name X` stanza.
    """
    would_be_commands = [f"vlan {vlan_id}", f" name {vlan_name}"]
    running_config = ""
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.warning("vlan_conflict_precheck_read_failed", vlan_id=vlan_id, error=str(exc))

    existing = find_existing_block(would_be_commands, running_config) if running_config else None
    if existing:
        return {
            "existing_entity": existing["anchor"],
            "existing_block": existing["block"],
            "is_exact_match": existing["is_exact_match"],
        }

    # Fallback: show_vlan_brief is authoritative for VLAN existence + name on
    # C1111-4P style devices where VLAN config lives in vlan.dat only.
    try:
        vlans = read_tools.show_vlan_brief()
    except Exception as exc:
        log.warning("vlan_conflict_vlan_brief_failed", vlan_id=vlan_id, error=str(exc))
        return None
    for v in vlans:
        if not isinstance(v, dict):
            continue
        if str(v.get("vlan_id", "")).strip() != str(vlan_id):
            continue
        # ntc-templates emits `vlan_name` (NOT `name`) for the
        # cisco_ios_show_vlan_brief template. See
        # backend/webui_agent/verify.py:49 for the same gotcha.
        existing_name = (v.get("vlan_name") or "").strip()
        synthetic_block = (
            f"vlan {vlan_id}\n name {existing_name}" if existing_name else f"vlan {vlan_id}"
        )
        log.info(
            "vlan_conflict_vlan_brief_match",
            vlan_id=vlan_id,
            existing_name=existing_name,
            requested_name=vlan_name,
        )
        return {
            "existing_entity": f"vlan {vlan_id}",
            "existing_block": synthetic_block,
            "is_exact_match": existing_name == vlan_name,
        }
    return None


def _propose_set_hostname(new_name: str) -> dict:
    # Validate at propose-time so the chat reply fails fast (HTTP 422 via
    # the planner) instead of creating an action_id that can only error
    # out later at execute time. Same validators the write tool will
    # re-run server-side — defense-in-depth, but the user-facing failure
    # mode is the cheap one.
    _validate_hostname(new_name)

    # params contains ONLY the executor's kwargs — no propose-time metadata.
    # (`set_hostname(new_name: str, action_id: str)`)
    params: dict[str, Any] = {"new_name": new_name}
    preview_meta = _detect_hostname_conflict(new_name)

    action_id = propose_action("set_hostname", params, preview_meta=preview_meta)
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": f"Will run: 'hostname {new_name}' on the C1111",
        "execute_tool": "set_hostname",
        "execute_params": {"new_name": new_name, "action_id": action_id},
        "next_step": _NEXT_STEP_INLINE + " No need to open another screen.",
        "commands": [f"hostname {new_name}"],
        "preview_meta": preview_meta,
    }


_SWITCHPORT_RE = re.compile(r"^\s*switchport(\s|$)", re.MULTILINE)


def _derive_svi_vlan_id(ip: str) -> int:
    """Pick a VLAN id for the auto-redirected SVI plan.

    Heuristic: third octet of the requested IP — works for the common
    `192.168.<N>.<host>/24` lab layout (192.168.40.1 -> VLAN 40). When
    the third octet is 0 or 1 (e.g. 10.0.0.x, anything on VLAN 1), fall
    back to 100 so we never collide with the default VLAN or produce an
    out-of-range id. Operator can rename/re-VLAN later if they want.
    """
    try:
        third_octet = int(ip.split(".")[2])
    except (IndexError, ValueError):
        return 100
    if 2 <= third_octet <= 4094:
        return third_octet
    return 100


def _build_svi_redirect_proposal(interface: str, ip: str, mask: str) -> dict:
    """When `interface` is a hardware switchport, propose an SVI plan
    instead of the direct `set_interface_ip` write.

    Three IOS XE blocks bundled into one approval:
      1. `vlan <N>` + `name auto-vlan-<N>` (idempotent — re-applies if exists)
      2. `interface Vlan<N>` + `ip address <ip> <mask>` + `no shutdown`
      3. `interface <port>` + `switchport mode access` + `switchport access vlan <N>`

    Returns the same `awaiting_approval` shape as `_propose_cli_configure`
    so the chat/UI render it identically and execute via `cli_configure`.
    """
    vlan_id = _derive_svi_vlan_id(ip)
    vlan_name = f"auto-vlan-{vlan_id}"

    config_commands = [
        f"vlan {vlan_id}",
        f" name {vlan_name}",
        "exit",
        f"interface Vlan{vlan_id}",
        f" ip address {ip} {mask}",
        " no shutdown",
        "exit",
        f"interface {interface}",
        " switchport mode access",
        f" switchport access vlan {vlan_id}",
        " no shutdown",
    ]
    verify_command = f"show ip interface brief | include Vlan{vlan_id}"
    # `show ip interface brief` columns are width-padded; \s+ between
    # interface name and the address handles whatever spacing IOS XE uses.
    verify_pattern = rf"Vlan{vlan_id}\s+{re.escape(ip)}\s+"
    risk = (
        f"{interface} is a hardware switchport on the C1111-4P and cannot take "
        f"`ip address` directly. Applying via VLAN {vlan_id} SVI: creates VLAN "
        f"{vlan_id} if absent (named {vlan_name!r}); assigns {ip}/{mask} to "
        f"`interface Vlan{vlan_id}`; sets {interface} to "
        f"`switchport mode access` + `switchport access vlan {vlan_id}`. Any "
        f"prior trunk/voice/extra config on {interface} will be replaced; if "
        f"VLAN {vlan_id} already exists with a different SVI IP, that IP is "
        f"overwritten."
    )

    # Defense-in-depth — same validators every cli_configure plan goes through.
    _validate_config_commands(config_commands)
    _validate_verify_command(verify_command)
    _validate_verify_pattern(verify_pattern)

    intent = (
        f"Set {ip}/{mask} on {interface} (re-routed: port is L2-only on "
        f"this chassis, applying via VLAN {vlan_id} SVI)"
    )
    evidence = [
        {"source": "docs/router-prerequisites.md", "section": "C1111-4P Gi0/1/x L2-only"},
    ]
    action_id = propose_action(
        tool="cli_configure",
        params={
            "intent": intent,
            "config_commands": config_commands,
            "verify_command": verify_command,
            "verify_pattern": verify_pattern,
            "risk": risk,
            "evidence": evidence,
        },
    )
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "execute_tool": "cli_configure",
        "preview": {
            "intent": intent,
            "config_commands": config_commands,
            "verify_command": verify_command,
            "verify_pattern": verify_pattern,
            "risk": risk,
            "evidence": evidence,
            "command_count": len(config_commands),
        },
        "next_step": _NEXT_STEP_INLINE,
        "commands": config_commands,
    }


def _propose_set_interface_ip(interface: str, ip: str, mask: str) -> dict:
    _validate_interface(interface)
    _validate_interface_ip_and_mask(ip, mask)

    # Hardware pre-check: if the port is a switchport, `ip address` will be
    # rejected at write time. Re-route to a 3-step SVI plan that the operator
    # approves in one go. Snapshot-driven so it works across chassis without
    # a hardcoded port list. SSH failure (router down, unit tests) is a soft
    # miss — fall through to the direct propose; chunk-1's write-tool verify
    # still catches the silent-failure case at execute time.
    iface_block = ""
    try:
        iface_block = read_tools.show_running_config_interface(interface)
    except Exception as exc:
        log.warning(
            "propose_set_interface_ip_precheck_read_failed",
            interface=interface,
            error=str(exc),
        )

    if iface_block and _SWITCHPORT_RE.search(iface_block):
        return _build_svi_redirect_proposal(interface, ip, mask)

    would_be_commands = [f"interface {interface}", f" ip address {ip} {mask}"]
    running_config = ""
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.warning(
            "propose_set_interface_ip_conflict_read_failed",
            interface=interface,
            error=str(exc),
        )

    # params contains ONLY the executor's kwargs — no propose-time metadata.
    params: dict[str, Any] = {"interface": interface, "ip": ip, "mask": mask}
    existing = find_existing_block(would_be_commands, running_config) if running_config else None
    preview_meta: dict[str, Any] | None = None
    if existing:
        preview_meta = {
            "existing_entity": existing["anchor"],
            "existing_block": existing["block"],
            "is_exact_match": existing["is_exact_match"],
        }

    action_id = propose_action("set_interface_ip", params, preview_meta=preview_meta)
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": f"Will set {interface} -> {ip}/{mask}",
        "execute_tool": "set_interface_ip",
        "execute_params": {
            "interface": interface,
            "ip": ip,
            "mask": mask,
            "action_id": action_id,
        },
        "next_step": _NEXT_STEP_INLINE,
        "commands": would_be_commands,
        "preview_meta": preview_meta,
    }


def _propose_set_access_vlan(vlan_id: int, vlan_name: str) -> dict:
    _validate_vlan_id(vlan_id)
    _validate_vlan_name(vlan_name)

    # params contains ONLY the executor's kwargs — no propose-time metadata.
    params: dict[str, Any] = {"vlan_id": vlan_id, "vlan_name": vlan_name}
    preview_meta = _detect_vlan_conflict(vlan_id, vlan_name)

    action_id = propose_action("set_access_vlan", params, preview_meta=preview_meta)
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": (
            f"Will run: 'vlan {vlan_id}' + ' name {vlan_name}' in config mode on the C1111"
        ),
        "execute_tool": "set_access_vlan",
        "execute_params": {
            "vlan_id": vlan_id,
            "vlan_name": vlan_name,
            "action_id": action_id,
        },
        "next_step": _NEXT_STEP_INLINE,
        "commands": [f"vlan {vlan_id}", f" name {vlan_name}"],
        "preview_meta": preview_meta,
    }


def _propose_webui_set_hostname(new_name: str) -> dict:
    _validate_hostname(new_name)
    # Conflict detection runs against the same running-config the CLI fast-path
    # checks — the operator gets the same warning regardless of which transport
    # the LLM picks. `commands` field carries the IOS-equivalent so the
    # frontend's `IOS XE commands` block renders meaningfully even though the
    # actual write goes via WebUI clicks.
    preview_meta = _detect_hostname_conflict(new_name)
    # Store under `new_name` to match the flow function's kwarg name
    # (change_hostname_via_webui(new_name, action_id)).
    action_id = propose_action(
        "webui_set_hostname", {"new_name": new_name}, preview_meta=preview_meta
    )
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": f"Will drive WebUI: Administration → Device Properties → set hostname '{new_name}' → Apply",
        "execute_tool": "webui_set_hostname",
        "execute_params": {"new_name": new_name, "action_id": action_id},
        "next_step": _NEXT_STEP_WEBUI,
        "commands": [f"hostname {new_name}"],
        "preview_meta": preview_meta,
    }


def _propose_webui_add_access_vlan(vlan_id: int, vlan_name: str) -> dict:
    _validate_vlan_id(vlan_id)
    _validate_vlan_name(vlan_name)
    # Same VLAN conflict detection as the CLI fast-path — includes the
    # vlan.dat fallback via show_vlan_brief for C1111-4P style devices.
    preview_meta = _detect_vlan_conflict(vlan_id, vlan_name)
    action_id = propose_action(
        "webui_add_access_vlan",
        {"vlan_id": vlan_id, "vlan_name": vlan_name},
        preview_meta=preview_meta,
    )
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": (
            f"Will drive WebUI: Configuration → Layer 2 → VLAN → Add → "
            f"VLAN ID {vlan_id} / Name '{vlan_name}' → Save, then verify via "
            f"CLI 'show vlan brief'."
        ),
        "execute_tool": "webui_add_access_vlan",
        "execute_params": {
            "vlan_id": vlan_id,
            "vlan_name": vlan_name,
            "action_id": action_id,
        },
        "next_step": _NEXT_STEP_WEBUI,
        "commands": [f"vlan {vlan_id}", f" name {vlan_name}"],
        "preview_meta": preview_meta,
    }


def _propose_cli_configure(**kwargs: Any) -> dict:
    """Propose an AI-drafted IOS XE configuration action.

    Flow: search_docs → show_running_config → draft_cli_plan → denylist
    + verify-command/pattern validators → propose_action. Returns
    awaiting_approval with the full command preview. Does NOT touch the
    router for writes.
    """
    intent = kwargs.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        return {"error": "bad_parameters", "message": "intent must be a non-empty string"}

    # 1. RAG grounding — small top_k (3) keeps tokens tight; CLI syntax
    # tends to live in a single section per feature.
    rag_result = _search_docs(query=intent, top_k=3)
    if "error" in rag_result:
        return rag_result
    rag_chunks = rag_result.get("results", [])

    # 2. Snapshot current running-config for the inner planner. We do this
    # via read_tools (not take_snapshot, which is execute-time) so a
    # propose call doesn't litter the snapshots directory.
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.error("propose_cli_configure_show_running_failed", error=str(exc))
        return {
            "error": "show_running_failed",
            "message": f"could not fetch running-config: {exc}",
        }

    # 3. Inner Haiku drafts the plan
    try:
        drafted = draft_cli_plan(intent, rag_chunks, running_config)
    except AnthropicOverloadedError as exc:
        request_id = getattr(exc, "request_id", None)
        log.warning("propose_cli_configure_llm_overloaded", intent=intent, request_id=request_id)
        return {
            "error": "llm_overloaded",
            "message": "The drafting LLM (Haiku) is temporarily overloaded. Please retry in a minute.",
            "request_id": request_id,
        }
    except RuntimeError as exc:
        log.error("propose_cli_configure_draft_failed", intent=intent, error=str(exc))
        return {"error": "draft_failed", "message": str(exc)}

    config_commands = drafted.get("config_commands") or []
    verify_command = drafted.get("verify_command", "")
    verify_pattern = drafted.get("verify_pattern", "")
    risk = drafted.get("risk", "")

    if not config_commands:
        # Inner LLM refused (non-CLI intent or unmappable). Surface to the
        # outer planner so it can re-prompt the user or fall back.
        return {
            "error": "intent_not_mappable",
            "message": risk or "Inner LLM did not produce a config plan.",
            "evidence": [
                {"source": c.get("source"), "section": c.get("section")} for c in rag_chunks
            ],
        }

    # 4. Server-side denylist + verify gate. Runs BEFORE propose_action so
    # the human never sees a preview containing a banned command. Same
    # validators re-run at execute time inside write_tools.cli_configure
    # (defense in depth — a tampered action dict still gets caught).
    try:
        _validate_config_commands(config_commands)
        _validate_verify_command(verify_command)
        _validate_verify_pattern(verify_pattern)
    except ValueError as exc:
        log.warning(
            "propose_cli_configure_unsafe",
            intent=intent,
            error=str(exc),
            drafted_commands=config_commands,
        )
        return {
            "error": "unsafe_command",
            "message": str(exc),
            "drafted_commands": config_commands,
            "verify_command": verify_command,
            "verify_pattern": verify_pattern,
            "evidence": [
                {"source": c.get("source"), "section": c.get("section")} for c in rag_chunks
            ],
        }

    evidence = [{"source": c.get("source"), "section": c.get("section")} for c in rag_chunks]

    # Conflict detection — preview_meta carries conflict fields separately from
    # cli_params so executor's func(**params) never receives unexpected kwargs.
    existing = find_existing_block(config_commands, running_config)
    cli_params: dict[str, Any] = {
        "intent": intent,
        "config_commands": config_commands,
        "verify_command": verify_command,
        "verify_pattern": verify_pattern,
        "risk": risk,
        "evidence": evidence,
    }
    preview_meta: dict[str, Any] | None = None
    if existing:
        preview_meta = {
            "existing_entity": existing["anchor"],
            "existing_block": existing["block"],
            "is_exact_match": existing["is_exact_match"],
        }

    action_id = propose_action(tool="cli_configure", params=cli_params, preview_meta=preview_meta)

    preview: dict[str, Any] = {
        "intent": intent,
        "config_commands": config_commands,
        "verify_command": verify_command,
        "verify_pattern": verify_pattern,
        "risk": risk,
        "evidence": evidence,
        "command_count": len(config_commands),
    }

    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "execute_tool": "cli_configure",
        "preview": preview,
        "next_step": _NEXT_STEP_INLINE,
        "preview_meta": preview_meta,
        "commands": config_commands,
    }


def _cli_configure(**kwargs: Any) -> dict:
    """Dispatcher wrapper that pulls executable params out of the stored
    action and calls the actual ``write_tools.cli_configure`` executor.

    The stored action dict includes preview-only fields (intent, risk,
    evidence) that the executor doesn't need; this wrapper keeps the
    executor's signature narrow while letting the preview be rich.
    """
    from backend.orchestration.confirmations import get_action

    action_id = kwargs.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        return {"error": "bad_parameters", "message": "action_id must be a non-empty string"}

    try:
        action = get_action(action_id)
    except KeyError:
        return {"error": "unknown_action", "message": f"no action with id {action_id!r}"}

    params = action.get("params", {})
    return write_tools.cli_configure(
        action_id=action_id,
        config_commands=params.get("config_commands", []),
        verify_command=params.get("verify_command", ""),
        verify_pattern=params.get("verify_pattern", ""),
    )


# ---------------------------------------------------------------------------
# Chunk C4 — Atlas-path propose/execute (NO re-plan at execute)
#
# These are the ACTIVE functions wired into _TOOL_FUNCS (see dispatch switch
# below).
# ---------------------------------------------------------------------------


def _device_fingerprint_for_session() -> str:
    """Build a device fingerprint from show_version (best-effort).

    Returns ``"unknown__unknown"`` on any error so the atlas path degrades
    gracefully when SSH is down or the fingerprint module is unavailable.
    """
    try:
        from backend.cli_agent import read_tools as _rt  # noqa: PLC0415
        from backend.webui_agent.atlas.fingerprint import device_fingerprint  # noqa: PLC0415

        return device_fingerprint(_rt.show_version())
    except Exception:  # noqa: BLE001
        return "unknown__unknown"


def _atlas_from_view(view: dict[str, Any]) -> Any:  # returns RouteAtlas
    """Build a minimal RouteAtlas from a webui_perceive view dict.

    The perceive view's ``fields`` list already carries the key/label/role/
    widget/options/required that the atlas validator needs.  This helper
    converts those dicts to ``FieldSpec`` / ``ControlSpec`` objects so that
    ``validate_atlas_plan`` (which expects a ``RouteAtlas``) can run against
    a live-perceived page without requiring a pre-stored atlas file.
    """
    from backend.webui_agent.atlas.schema import (  # noqa: PLC0415
        ControlSpec,
        FieldSpec,
        RouteAtlas,
    )

    route = view.get("route", "")
    fp = view.get("device_fingerprint", "unknown__unknown")

    fields: list[FieldSpec] = []
    for f in view.get("fields", []):
        if not isinstance(f, dict):
            continue
        fkey = str(f.get("key") or "").strip()
        flabel = str(f.get("label") or f.get("name") or fkey).strip()
        frole = str(f.get("role") or "textbox").strip()
        fwidget = str(f.get("widget") or "input").strip()
        foptions = f.get("options")
        frequired = bool(f.get("required", False))
        if not fkey:
            continue
        fields.append(
            FieldSpec(
                key=fkey,
                label=flabel,
                role=frole,
                widget=fwidget,
                options=list(foptions) if foptions else None,
                required=frequired,
            )
        )

    apply_controls: list[ControlSpec] = []
    for c in view.get("apply_controls", []):
        if not isinstance(c, dict):
            continue
        ckey = str(c.get("key") or "").strip()
        clabel = str(c.get("label") or c.get("name") or ckey).strip()
        crole = str(c.get("role") or "button").strip()
        if not ckey:
            continue
        apply_controls.append(ControlSpec(key=ckey, label=clabel, role=crole))

    return RouteAtlas(
        route=route,
        device_fingerprint=fp,
        fields=fields,
        apply_controls=apply_controls,
    )


# Bounded open-form retries: the Cisco WebUI SPA occasionally loads with its
# AngularJS controllers unbound, so the Add-button click times out and the plan
# comes back empty.  A page reload re-bootstraps Angular; this caps how many
# reload-and-retry rounds the propose path makes before giving up (so a
# deterministically-broken page still fails fast rather than looping).
_OPEN_FORM_MAX_ATTEMPTS = 3


def _propose_webui_configure_atlas(**kwargs: Any) -> dict:
    """Atlas-path propose: perceive (one a11y snapshot) → draft_atlas_plan
    (typed, validated) → propose_action.  Returns the same awaiting_approval
    shape as the legacy function so the frontend + outer planner are unchanged.

    Key properties vs. the legacy path:
    - One webui_perceive call (no webui_describe_page, no networkidle).
    - draft_atlas_plan produces ``{field_key, value}`` steps, not intent-dicts.
    - At execute time EXACTLY these validated steps run — no re-plan.
    """
    intent = kwargs.get("intent")
    webui_path = kwargs.get("webui_path")
    if not isinstance(intent, str) or not intent.strip():
        return {"error": "bad_parameters", "message": "intent must be a non-empty string"}
    if not isinstance(webui_path, str) or not webui_path.strip():
        return {"error": "bad_parameters", "message": "webui_path must be a non-empty string"}

    # 1. RAG grounding
    rag_result = _search_docs(query=intent, top_k=3)
    if "error" in rag_result:
        return rag_result
    rag_chunks = rag_result.get("results", [])

    # 2. Best-effort device fingerprint (SSH may be down — keep going)
    fp = _device_fingerprint_for_session()

    # 3. Open the WebUI page — no action_id yet (propose_action runs after)
    open_result = webui_open(path=webui_path)
    if "error" in open_result:
        return open_result
    session_id = open_result["session_id"]

    # 4. Perceive: single a11y snapshot — this replaces both describe_page and
    #    the networkidle wait that made the legacy path so slow.
    perceive_result = webui_perceive(
        session_id=session_id,
        route=webui_path,
        device_fingerprint=fp,
    )
    if "error" in perceive_result:
        close_all_sessions()
        return perceive_result
    view = perceive_result["view"]

    # 5. Open-form probe: if the perceived view has no fields but an open-form
    #    control is visible, click it and re-perceive — same heuristic as the
    #    legacy path, adapted for the atlas view shape.
    #
    #    The atlas view uses ``apply_controls`` (submit buttons) and ``fields``
    #    (fillable elements).  A list-page has apply_controls=[] and fields=[]
    #    but may have an "Add" button in the unmapped list.  We also check
    #    the open_form_control the perceive engine may surface directly.
    #
    #    Gate: the form is NOT open yet iff NO Apply/submit control is visible.
    #    We do NOT gate on field count: a Cisco list page renders a Kendo grid
    #    that can leak a stray select/filter control (DHCP's "Monitoring"
    #    row-checkbox), so ``not _view_fields`` is an unreliable "form closed"
    #    signal.  ``is_apply_control`` is glyph-based (pl-save / icon-save-device
    #    / primaryActionButton) and only fires on a rendered form's save button,
    #    so ``not _has_submit`` is the trustworthy "form not open" discriminator:
    #    a list page surfaces no Apply control; an open form always does.  This
    #    also protects the OSPF re-perceive path — once the form is open the
    #    Apply control is present, so the Add click is correctly skipped (no
    #    double-Add).
    _view_fields: list[dict] = view.get("fields") or []
    _view_apply: list[dict] = view.get("apply_controls") or []
    _view_unmapped: list[dict] = view.get("unmapped") or []
    _open_form_ctrl = view.get("open_form_control")

    _has_submit = any(
        str(_c.get("label") or _c.get("name") or "").strip().lower() in _FORM_SUBMIT_NAMES_LOWER
        for _c in _view_apply
    )
    _trigger_key: str | None = None
    _trigger_label: str | None = None
    _trigger_role: str | None = None

    if not _has_submit:
        # Primary: the atlas-surfaced open_form_control (the "Add"/"Create"
        # button on a list page). reconcile now puts this in the view; without
        # it the form never opens and the plan comes back empty on OSPF/DHCP.
        if isinstance(_open_form_ctrl, dict) and (
            _open_form_ctrl.get("label") or _open_form_ctrl.get("name")
        ):
            _trigger_key = str(_open_form_ctrl.get("key") or "").strip() or None
            _trigger_label = str(
                _open_form_ctrl.get("label") or _open_form_ctrl.get("name") or ""
            ).strip()
            _trigger_role = str(_open_form_ctrl.get("role") or "button").strip()
        else:
            # Fallback: scan apply_controls + unmapped for a trigger-name button.
            _candidates = list(_view_apply) + list(_view_unmapped)
            for _cand in _candidates:
                _cname = str(_cand.get("label") or _cand.get("name") or "").strip().lower()
                _crole = str(_cand.get("role") or "").lower()
                if _cname in _FORM_TRIGGER_NAMES_LOWER and _crole in ("button", "link", ""):
                    _trigger_key = str(_cand.get("key") or "").strip() or None
                    _trigger_label = str(_cand.get("label") or _cand.get("name") or "").strip()
                    _trigger_role = str(_cand.get("role") or "button").strip()
                    break

    _form_opened = False
    if _trigger_label is not None:
        _open_intent: dict = {
            "role": _trigger_role or "button",
            "name": _trigger_label,
            "action": "click",
        }
        # The Cisco WebUI AngularJS SPA occasionally loads with its controllers
        # unbound (browser_pageerror "reading 'controller'/'service'"): the page
        # renders no form and the Add button never becomes actionable, so the
        # open-form click times out (click_timeout_unsafe_retry) and the plan
        # comes back empty -> the planner then mis-advises a CLI fallback.  A
        # page reload re-bootstraps Angular; both the reload and the open-form
        # click are READ-ONLY (no router write), so retrying after a reload is
        # safe.  Bounded by _OPEN_FORM_MAX_ATTEMPTS so a deterministically-broken
        # page still fails fast.
        for _attempt in range(1, _OPEN_FORM_MAX_ATTEMPTS + 1):
            try:
                _form_result = webui_open_form_for_planning(session_id, _open_intent)
            except Exception as _open_exc:  # noqa: BLE001
                log.warning(
                    "propose_webui_configure_atlas_form_open_exception",
                    intent=intent,
                    attempt=_attempt,
                    error=str(_open_exc),
                )
                _form_result = {"ok": False, "failure_reason": "open_form_exception"}

            if _form_result.get("ok"):
                _form_perceive = webui_perceive(
                    session_id=session_id,
                    route=webui_path,
                    device_fingerprint=fp,
                )
                if "error" not in _form_perceive:
                    view = _form_perceive["view"]
                    _form_opened = True
                    log.info(
                        "propose_webui_configure_atlas_opened_form",
                        intent=intent,
                        trigger_label=_trigger_label,
                        attempt=_attempt,
                    )
                    break
                log.warning(
                    "propose_webui_configure_atlas_reperceive_after_form_open_failed",
                    intent=intent,
                    attempt=_attempt,
                    error=_form_perceive.get("message"),
                )
            else:
                log.warning(
                    "propose_webui_configure_atlas_form_open_click_failed",
                    intent=intent,
                    attempt=_attempt,
                    failure_reason=_form_result.get("failure_reason"),
                )

            # Open-form didn't take this round.  If attempts remain, reload the
            # page (read-only re-navigation re-bootstraps the SPA) + re-perceive,
            # then retry the open-form click against the fresh page.
            if _attempt < _OPEN_FORM_MAX_ATTEMPTS:
                _reload = webui_reload_for_planning(session_id, webui_path)
                if "error" in _reload:
                    log.warning(
                        "propose_webui_configure_atlas_reload_failed",
                        intent=intent,
                        attempt=_attempt,
                        error=_reload.get("message") or _reload.get("error"),
                    )
                    break
                _reperceive = webui_perceive(
                    session_id=session_id,
                    route=webui_path,
                    device_fingerprint=fp,
                )
                if "error" not in _reperceive:
                    view = _reperceive["view"]
                log.info(
                    "propose_webui_configure_atlas_form_open_retry_after_reload",
                    intent=intent,
                    attempt=_attempt,
                )

    # 6. Running-config for conflict detection (soft-fail)
    running_config = ""
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:  # noqa: BLE001
        log.warning("propose_webui_configure_atlas_running_config_failed", error=str(exc))

    # 7. Build a minimal RouteAtlas from the perceive view so validate_atlas_plan
    #    can type-check the LLM's output against the live-perceived fields.
    atlas = _atlas_from_view(view)

    # 8. Atlas-typed plan draft (raises RuntimeError on LLM/parse failure)
    try:
        drafted = draft_atlas_plan(
            intent,
            rag_chunks,
            view,
            atlas,
            running_config=running_config,
        )
    except AnthropicOverloadedError as exc:
        request_id = getattr(exc, "request_id", None)
        log.warning(
            "propose_webui_configure_atlas_llm_overloaded",
            intent=intent,
            request_id=request_id,
        )
        close_all_sessions()
        return {
            "error": "llm_overloaded",
            "message": "The drafting LLM (Haiku) is temporarily overloaded. Please retry in a minute.",
            "request_id": request_id,
        }
    except RuntimeError as exc:
        log.error("propose_webui_configure_atlas_draft_failed", intent=intent, error=str(exc))
        close_all_sessions()
        return {"error": "draft_failed", "message": str(exc)}

    typed_plan: list[dict[str, Any]] = drafted["plan"]  # [{field_key, value}, ...]
    verify_text = drafted["verify_text"]
    risk = drafted["risk"]
    equivalent_cli = drafted.get("equivalent_cli_commands") or []
    validation_errors = drafted.get("validation_errors") or []
    # Page-agnostic post-apply success signal captured per-route (a11y_text
    # "success").  The executor falls back to this when the planner supplies no
    # verify_text, so an apply is never silently marked clean without any
    # post-write confirmation.
    success_signal_contains: str | None = view.get("success_signal_contains")

    if not typed_plan:
        # Visibility breadcrumb for the WebUI->CLI fallback: without this the
        # propose->intent_not_mappable->CLI causal chain leaves no log trace and
        # diagnosis required dumping the captured atlas.  Carry the perceived
        # field_keys, whether the open-form probe fired, and the dropped
        # field_keys/values from validation (not just counts).
        log.warning(
            "propose_webui_configure_atlas_empty_plan",
            intent=intent,
            webui_path=webui_path,
            field_keys=[str(f.get("key")) for f in (view.get("fields") or [])],
            form_opened=_form_opened,
            validation_errors=validation_errors,
        )
        close_all_sessions()
        return {
            "error": "intent_not_mappable",
            "message": risk,
            "evidence": [
                {"source": c.get("source"), "section": c.get("section")} for c in rag_chunks
            ],
            "validation_errors": validation_errors,
        }

    # 9. Build display-compatible steps for the frontend (action/intent/value shape)
    #    PLUS carry field_key for the executor.  The executor reads field_key; the
    #    frontend renders action + intent + value (unchanged contract).
    display_steps: list[dict[str, Any]] = []
    for step in typed_plan:
        fk = step["field_key"]
        val = step["value"]
        field = atlas.field_by_key(fk)
        if field is None:
            # Should not happen after validate_atlas_plan, but be defensive.
            action_name = "fill"
            frole = "textbox"
            flabel = fk
        else:
            frole = field.role
            flabel = field.label
            widget = field.widget
            # Map widget/role to a human-readable action name for the frontend.
            if widget in ("kendo_combobox",) or frole in ("combobox", "listbox"):
                action_name = "select"
            elif widget in ("checkbox", "radio") or frole in ("checkbox", "radio"):
                action_name = "check"
            else:
                action_name = "fill"
        display_steps.append(
            {
                "action": action_name,
                "intent": {"role": frole, "name": flabel},
                "value": val,
                "field_key": fk,
            }
        )

    # Append the apply-control step (executor uses apply_key; frontend renders it)
    apply_key: str | None = None
    apply_label = "Apply"
    apply_role = "button"
    if atlas.apply_controls:
        apply_ctrl = atlas.apply_controls[0]
        apply_key = apply_ctrl.key
        apply_label = apply_ctrl.label
        apply_role = apply_ctrl.role
    display_steps.append(
        {
            "action": "click",
            "intent": {"role": apply_role, "name": apply_label},
            "value": None,
            "apply_key": apply_key,
        }
    )

    # 10. Conflict detection (same as legacy path — soft-fail)
    existing = None
    if equivalent_cli and running_config:
        existing = find_existing_block(equivalent_cli, running_config)

    evidence = [{"source": c.get("source"), "section": c.get("section")} for c in rag_chunks]

    # 11. Register the action.  params carries ONLY what the executor needs.
    #     display_steps (with field_key + apply_key) IS the plan the executor runs.
    webui_params: dict[str, Any] = {
        "intent": intent,
        "webui_path": webui_path,
        "plan": display_steps,
        "verify_text": verify_text,
        "success_signal_contains": success_signal_contains,
        "risk": risk,
        "evidence": evidence,
        "session_id": session_id,
        "device_fingerprint": fp,
        "equivalent_cli_commands": equivalent_cli,
        "apply_key": apply_key,
    }
    preview_meta: dict[str, Any] | None = None
    if existing:
        preview_meta = {
            "existing_entity": existing["anchor"],
            "existing_block": existing["block"],
            "is_exact_match": existing["is_exact_match"],
        }

    action_id = propose_action(
        tool="webui_configure", params=webui_params, preview_meta=preview_meta
    )

    webui_preview: dict[str, Any] = {
        "intent": intent,
        "plan": display_steps,
        "verify_text": verify_text,
        "risk": risk,
        "evidence": evidence,
        "step_count": len(display_steps),
        "validation_errors": validation_errors,
    }

    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "execute_tool": "webui_configure",
        "preview": webui_preview,
        "next_step": _NEXT_STEP_WEBUI,
        "preview_meta": preview_meta,
    }


def _webui_configure_atlas(**kwargs: Any) -> dict:
    """Execute the stored atlas-typed plan.  NO re-plan at execute time.

    The operator approved an exact set of field fills + one apply click.
    This function runs EXACTLY those steps via webui_act_field +
    webui_apply_control + webui_verify_a11y.  No draft_atlas_plan /
    draft_plan invocation here — that's the inner_plan_empty regression lock.

    Convergence guard: if the SAME (field_key, failure_reason) pair fails
    twice, we abort with ``no_progress`` (same rule as the legacy path).
    """
    from backend.cli_agent.snapshots import take_snapshot  # noqa: PLC0415
    from backend.orchestration.confirmations import (  # noqa: PLC0415
        get_action,
        mark_executed,
        mark_failed,
    )

    action_id = kwargs.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        return {"error": "bad_parameters", "message": "action_id must be a non-empty string"}

    # HITL layer 2
    if not is_approved(action_id):
        return {"error": "not_approved", "message": f"action_id {action_id!r} is not APPROVED"}

    try:
        action = get_action(action_id)
    except KeyError:
        return {"error": "unknown_action", "message": f"no action with id {action_id!r}"}

    params = action.get("params", {})
    plan: list[dict[str, Any]] = params.get("plan") or []
    verify_text: str | None = params.get("verify_text")
    success_signal_contains: str | None = params.get("success_signal_contains")
    session_id: str | None = params.get("session_id")
    fp: str = params.get("device_fingerprint") or "unknown__unknown"
    webui_path: str = params.get("webui_path") or ""
    apply_key: str | None = params.get("apply_key")

    if not plan or not session_id:
        bad_params_result = {
            "error": "bad_action_params",
            "message": "action missing plan or session_id",
        }
        mark_failed(action_id, bad_params_result)
        return bad_params_result

    # Separate field-fill steps from the trailing apply step
    field_steps = [s for s in plan if s.get("field_key")]
    # apply_key in params is the canonical apply key; the apply step in plan is
    # just for display.  Use the params value (already validated at propose time).

    executed_steps: list[dict[str, Any]] = []

    # Re-perceive to refresh the child's current_atlas (so act_field locates
    # fields correctly) — one cheap a11y read, no networkidle.
    reperceive = webui_perceive(
        session_id=session_id,
        route=webui_path or None,
        device_fingerprint=fp,
    )
    if "error" in reperceive:
        mark_failed(action_id, reperceive)
        close_all_sessions()
        log.error(
            "webui_configure_atlas_reperceive_failed",
            action_id=action_id,
            error=reperceive,
        )
        return {
            "error": "reperceive_failed",
            "message": reperceive.get("message", "perceive failed at execute time"),
            "session_id": session_id,
        }

    # Pre-snapshot: capture the BEFORE state right before the first
    # router-affecting step, so the post-snapshot taken after a successful Apply
    # has a matching pre to diff against.  The propose-time webui_open is keyed
    # sess_<uuid> (no real action_id), so _maybe_pre_snapshot is skipped there;
    # this is the only place the real, approved action_id is in scope before the
    # write.  Best-effort: an SSH hiccup taking the snapshot must NOT abort the
    # operator-approved write.
    try:
        take_snapshot(action_id, "pre")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "webui_configure_atlas_pre_snapshot_failed",
            action_id=action_id,
            error=str(exc),
        )

    # Convergence guard: same (field_key, failure_reason) fails twice in the
    # plan → no_progress (the plan is circular or the field is unmappable).
    # Unlike the legacy multi-iteration path, we process ALL field steps in a
    # single pass and collect failures before deciding.  This lets the guard
    # see both occurrences of the same key in one batch without needing a
    # second execution attempt.
    _step_failure_counts: dict[tuple[str, str], int] = {}
    first_failure: dict[str, Any] | None = None

    for step in field_steps:
        fk = step["field_key"]
        val = step.get("value")

        step_result = webui_act_field(
            session_id=session_id,
            field_key=fk,
            value=val,
            action_id=action_id,
        )
        ok = step_result.get("ok") is True and "error" not in step_result
        executed_steps.append(
            {
                "field_key": fk,
                "value": val,
                "result": step_result,
                "status": "ok" if ok else "failed",
            }
        )

        if not ok:
            failure_reason = step_result.get("failure_reason") or step_result.get("error", "")
            _failure_key: tuple[str, str] = (fk, str(failure_reason))
            _step_failure_counts[_failure_key] = _step_failure_counts.get(_failure_key, 0) + 1

            log.warning(
                "webui_configure_atlas_step_failed",
                action_id=action_id,
                field_key=fk,
                failure_reason=failure_reason,
                failure_count=_step_failure_counts[_failure_key],
            )

            if _step_failure_counts[_failure_key] >= 2:
                no_progress_result = {
                    "error": "no_progress",
                    "message": (
                        f"Field '{fk}' failed with '{failure_reason}' "
                        f"{_step_failure_counts[_failure_key]} times in this plan — "
                        "aborting to avoid spinning."
                    ),
                    "field_key": fk,
                    "failure_reason": failure_reason,
                    "failure_count": _step_failure_counts[_failure_key],
                    "completed_steps": executed_steps,
                }
                # Pass the failure context so debug_sweep can do a focused
                # diagnosis instead of falling back to a generic sweep.
                mark_failed(action_id, no_progress_result)
                close_all_sessions()
                log.error(
                    "webui_configure_atlas_no_progress",
                    action_id=action_id,
                    field_key=fk,
                    failure_reason=failure_reason,
                    failure_count=_step_failure_counts[_failure_key],
                )
                return no_progress_result

            # Record the first failure encountered (for the step_failed path).
            if first_failure is None:
                first_failure = {
                    "field_key": fk,
                    "failure_reason": failure_reason,
                    "result": step_result,
                }

    if first_failure is not None:
        # At least one field step failed (but not no_progress).  No apply.
        mark_failed(action_id, first_failure)
        close_all_sessions()
        return {
            "error": "step_failed",
            "message": (
                f"Field '{first_failure['field_key']}' failed: {first_failure['failure_reason']}"
            ),
            "failed_step": first_failure,
            "completed_steps": executed_steps,
        }

    # All field steps succeeded — click Apply.
    apply_result = webui_apply_control(
        session_id=session_id,
        action_id=action_id,
        key=apply_key,
    )
    apply_ok = apply_result.get("ok") is True and "error" not in apply_result
    if not apply_ok:
        failure_reason = apply_result.get("failure_reason") or apply_result.get("error", "")
        # NEVER retry apply (CLAUDE.md §4: click_timeout_unsafe_retry is terminal).
        mark_failed(action_id, apply_result)
        close_all_sessions()
        log.error(
            "webui_configure_atlas_apply_failed",
            action_id=action_id,
            apply_key=apply_key,
            failure_reason=failure_reason,
        )
        return {
            "error": "apply_failed",
            "message": f"Apply control failed: {failure_reason}",
            "failure_reason": failure_reason,
            "apply_result": apply_result,
            "completed_steps": executed_steps,
        }

    # Verify (a11y-based — no webui_describe_page / networkidle).
    # Target precedence: the planner-supplied verify_text (specific, e.g. a pool
    # name / VLAN id), else the atlas success_signal ("success" banner) so a
    # write is never marked clean without ANY post-write confirmation.  When
    # BOTH are absent (a pure settings/toggle page), we still mark_executed but
    # flag verified=False and warn — never report it as a confirmed clean write.
    _verify_target: str | None = verify_text or success_signal_contains
    verify_result: dict[str, Any] | None = None
    verified = False
    if _verify_target:
        import time as _time  # noqa: PLC0415

        # Poll briefly: after Apply the Cisco list page needs a moment to
        # re-render (close the modal, repaint the grid), so a single immediate
        # check can false-negative on a write that actually landed. ~3s total.
        _verify_present = False
        for _attempt in range(4):
            verify_result = webui_verify_a11y(session_id=session_id, contains=_verify_target)
            if verify_result.get("present"):
                _verify_present = True
                break
            if _attempt < 3:
                _time.sleep(0.75)
        if not _verify_present:
            # Verify failed — do NOT mark_executed; surface as verify_failed.
            mark_failed(action_id, verify_result)
            close_all_sessions()
            log.error(
                "webui_configure_atlas_verify_failed",
                action_id=action_id,
                verify_text=_verify_target,
                verify_result=verify_result,
            )
            return {
                "error": "verify_failed",
                "message": f"verify_a11y did not find {_verify_target!r} after apply",
                "verify_text": _verify_target,
                "verify_result": verify_result,
                "completed_steps": executed_steps,
            }
        verified = True

    # Success — mark executed, take POST-snapshot, clean up.
    mark_executed(action_id)
    if not verified:
        # Apply succeeded but we had no target to confirm the write landed.
        # Do NOT report this as a confirmed clean success — flag it for the
        # operator and the logs (visibility-first).
        log.warning(
            "webui_configure_atlas_unverified",
            action_id=action_id,
            steps_run=len(executed_steps),
        )
    try:
        take_snapshot(action_id, "post")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "webui_configure_atlas_post_snapshot_failed",
            action_id=action_id,
            error=str(exc),
        )
    close_all_sessions()

    log.info(
        "webui_configure_atlas_complete",
        action_id=action_id,
        steps_run=len(executed_steps),
        verify_text=_verify_target,
        verified=verified,
    )
    return {
        "ok": True,
        "action_id": action_id,
        "completed_steps": executed_steps,
        "verify_result": verify_result,
        "verified": verified,
        "snapshot_post": None,  # take_snapshot writes files; path is artifacts-side
    }


def _propose_debug_sweep(**kwargs: Any) -> dict:
    """Propose a diagnostic show plan. Reactive (failure context found) or
    on-demand (no recent failure). Returns awaiting_approval shape.

    Reactive failure context comes from one of three sources (tried in order):
    1. `failure_action_id` kwarg passed by the LLM (ideal).
    2. The most-recently-FAILED action in confirmations (server-side fallback).
       This catches the auto-debug case where the LLM didn't extract the
       action_id from the user's "Please diagnose action_id=X..." message.
    3. None → broad on-demand sweep.

    The fallback is what keeps reactive diagnosis focused even when Haiku
    omits the kwarg. Without it the sweep degrades to a generic health check
    that misses the actual failure the operator wanted explained.
    """
    from backend.orchestration.confirmations import find_most_recent_failure, get_action
    from backend.orchestration.debug_planner import draft_debug_plan, draft_debug_sweep

    failure_action_id: str | None = kwargs.get("failure_action_id") or None

    failure_context: dict | None = None
    if failure_action_id:
        try:
            failed_action = get_action(failure_action_id)
        except KeyError:
            return {
                "error": "unknown_action",
                "message": f"no action {failure_action_id!r}",
            }
        # Pull the stored result dict (set by mark_failed extension)
        failure_context = failed_action.get("result") or None
        if failure_context is None:
            # Action exists but no result stored (e.g. action never failed).
            # Surface cleanly rather than drafting a diagnosis for nothing.
            return {
                "error": "no_failure_to_diagnose",
                "message": f"action {failure_action_id!r} has no stored failure context",
            }
    else:
        # Server-side fallback: LLM didn't pass failure_action_id, but there
        # may STILL be a recent failure worth focused diagnosis. Pull the
        # most-recently FAILED action's stored result. If none exists, we
        # fall through naturally to broad-sweep mode below.
        failure_context = find_most_recent_failure()
        if failure_context:
            log.info(
                "propose_debug_sweep_fallback_used",
                error_key=failure_context.get("error"),
                tool=failure_context.get("tool"),
            )

    try:
        drafted = draft_debug_plan(failure_context) if failure_context else draft_debug_sweep()
    except Exception as exc:
        log.error("propose_debug_sweep_draft_failed", error=str(exc))
        return {"error": "draft_failed", "message": str(exc)}

    commands = drafted.get("commands") or []
    if not commands:
        return {
            "error": "no_diagnostic_plan",
            "message": drafted.get("summary_intent") or "Could not draft a diagnostic plan",
        }

    # Validate each command starts with `show ` for safety (defense in depth)
    for cmd in commands:
        if not isinstance(cmd, str) or not cmd.strip().lower().startswith("show "):
            return {
                "error": "unsafe_command",
                "message": f"diagnostic plan included non-show command: {cmd!r}",
            }

    params = {
        "commands": commands,
        "failure_action_id": failure_action_id,  # may be None for on-demand
    }
    action_id = propose_action("debug_sweep", params, preview_meta=None)
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "execute_tool": "debug_sweep",
        "preview": {
            "intent": drafted.get("summary_intent", "Diagnose router state"),
            "commands": commands,
            "risk": drafted.get("risk", "low — read-only show commands"),
        },
        "next_step": _NEXT_STEP_INLINE,
        "commands": commands,
        "preview_meta": None,
    }


def _debug_sweep(**kwargs: Any) -> dict:
    """Execute the approved diagnostic show plan. Runs each show via
    read_tools._run, collects outputs, hands them + failure context to
    Haiku for a digest. Returns the digest as the chat reply text."""
    from backend.orchestration.confirmations import get_action
    from backend.orchestration.debug_planner import draft_debug_summary

    action_id = kwargs.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        return {"error": "bad_parameters", "message": "action_id required"}
    try:
        action = get_action(action_id)
    except KeyError:
        return {"error": "unknown_action", "message": f"no action {action_id!r}"}

    params = action.get("params", {})
    commands = params.get("commands") or []
    failure_action_id = params.get("failure_action_id")

    # Pull the original failure context if reactive
    failure_context = None
    if failure_action_id:
        with contextlib.suppress(KeyError):
            failure_context = (get_action(failure_action_id).get("result")) or None

    # Run each show command via _run (use_textfsm=False — diagnostic
    # plans want the raw text, not parsed dicts). Cap individual output
    # at 4000 chars to keep prompt cost bounded.
    outputs: dict[str, str] = {}
    for cmd in commands:
        try:
            raw = read_tools._run(cmd, use_textfsm=False)
            outputs[cmd] = str(raw)[:4000]
        except Exception as exc:
            outputs[cmd] = f"<execution failed: {exc}>"

    try:
        digest = draft_debug_summary(outputs, failure_context)
    except Exception as exc:
        log.error("debug_sweep_summary_failed", error=str(exc))
        digest = (
            "Diagnostic outputs collected but the summary LLM call failed. "
            "Raw outputs follow:\n\n" + "\n\n".join(f"{c}:\n{o}" for c, o in outputs.items())
        )

    return {
        "tool": "debug_sweep",
        "summary": digest,
        "raw_outputs": outputs,
        # No snapshot_post — debug_sweep is read-only. The applied-event
        # heuristic in planner.py checks snapshot_post so it naturally
        # won't emit `applied` for this tool. That's correct — the
        # digest IS the operator-visible result.
    }


_TOOL_FUNCS: dict[str, Callable[..., Any]] = {
    "show_version": read_tools.show_version,
    "show_ip_interface_brief": read_tools.show_ip_interface_brief,
    "show_running_config": read_tools.show_running_config,
    "show_vlan_brief": read_tools.show_vlan_brief,
    "search_docs": _search_docs,
    "propose_set_hostname": _propose_set_hostname,
    "set_hostname": write_tools.set_hostname,
    "propose_set_interface_ip": _propose_set_interface_ip,
    "set_interface_ip": write_tools.set_interface_ip,
    "propose_set_access_vlan": _propose_set_access_vlan,
    "set_access_vlan": write_tools.set_access_vlan,
    "propose_webui_set_hostname": _propose_webui_set_hostname,
    "webui_set_hostname": change_hostname_via_webui,
    "propose_webui_add_access_vlan": _propose_webui_add_access_vlan,
    "webui_add_access_vlan": add_access_vlan_via_webui,
    # Phase 5 / Chunk C4 — generic AI-driven WebUI configure (two-step HITL).
    # Switched to atlas variants: perceive → draft_atlas_plan → execute with
    # NO re-plan (kills inner_plan_empty + >5-min latency).
    "propose_webui_configure": _propose_webui_configure_atlas,
    "webui_configure": _webui_configure_atlas,
    # CLI AI configure — same propose/execute split. Inner Haiku drafts
    # IOS XE commands grounded in RAG + running-config; denylist filters
    # at propose AND execute time.
    "propose_cli_configure": _propose_cli_configure,
    "cli_configure": _cli_configure,
    # Chunk 12 — diagnostic sweep. propose_debug_sweep drafts the plan
    # (reactive: failure_action_id set, or on-demand: no arg). debug_sweep
    # executes the approved show commands and returns a Haiku digest.
    "propose_debug_sweep": _propose_debug_sweep,
    "debug_sweep": _debug_sweep,
}


def execute_tool(name: str, params: dict[str, Any]) -> dict:
    """Invoke a registered tool by name. Always returns a dict (never raises).

    Errors are wrapped into the return value so the planner can surface them
    to the user instead of crashing mid-tool-use-loop.
    """
    if name not in _TOOL_FUNCS:
        log.warning("unknown_tool", tool=name)
        return {"error": f"unknown tool: {name!r}", "available": list(_TOOL_FUNCS)}

    # Defense-in-depth layer 1: dispatcher refuses write tools whose action_id
    # is missing or not APPROVED, before the function is ever called.
    if name in _REQUIRES_APPROVAL:
        action_id = params.get("action_id")
        if not action_id or not is_approved(action_id):
            log.info("dispatcher_not_approved", tool=name, action_id=action_id)
            return {
                "error": "not_approved",
                "message": (
                    f"action_id {action_id!r} is not APPROVED; "
                    "call POST /api/approve/{action_id} first."
                ),
            }

    func = _TOOL_FUNCS[name]

    # Signature guard — refuse to splat params containing keys the target
    # function doesn't accept. Catches the bug class where display-only fields
    # leaked into action["params"] instead of preview_meta. The propose path
    # enforces preview_meta separation, but this layer is defense-in-depth.
    sig = inspect.signature(func)
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if not has_var_keyword:
        accepted = set(sig.parameters.keys())
        extras = set(params.keys()) - accepted
        if extras:
            log.warning(
                "tool_unexpected_params",
                tool=name,
                unexpected=sorted(extras),
                accepted=sorted(accepted),
            )
            return {
                "error": "bad_parameters",
                "message": (
                    f"execute_tool({name}) called with unexpected params: "
                    f"{sorted(extras)}. Function accepts: {sorted(accepted)}. "
                    "This usually means a display-only field was placed in "
                    "action['params'] instead of preview_meta."
                ),
            }

    try:
        result = func(**params)
    except NotApproved as exc:
        # Layer 2 still fires if approval was revoked between the dispatcher
        # check and the function call (race), or if a future tool is added
        # to _TOOL_FUNCS but forgotten in _REQUIRES_APPROVAL.
        log.info("tool_not_approved", tool=name, error=str(exc))
        return {"error": "not_approved", "message": str(exc)}
    except (TypeError, ValueError) as exc:
        # Wrong arguments or failed input validation (from propose-time
        # validators on hostname / interface / IP / mask / VLAN). Both
        # are "the input is wrong" — surface as bad_parameters so the
        # chat shows a useful message instead of a generic tool_failed.
        log.warning(
            "tool_bad_params",
            tool=name,
            params=params,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        return {"error": "bad_parameters", "message": str(exc)}
    except Exception as exc:
        # Some exceptions stringify to empty (bare Exception()). Always include
        # the exception class name so the operator has *something* to grep.
        msg = str(exc) or repr(exc) or type(exc).__name__
        log.error(
            "tool_exception",
            tool=name,
            exc_type=type(exc).__name__,
            error=msg,
            exc_info=True,
        )
        return {
            "error": "tool_failed",
            "exc_type": type(exc).__name__,
            "message": msg,
        }

    # Normalize to dict if a tool returns str/list
    if isinstance(result, dict):
        return result
    return {"result": result}


def tool_names() -> list[str]:
    """Return the list of registered tool names."""
    return list(_TOOL_FUNCS)
