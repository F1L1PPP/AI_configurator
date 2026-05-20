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

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

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
from backend.orchestration.configure_planner import draft_plan
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
    webui_act_by_intent,
    webui_describe_page,
    webui_open,
    webui_verify,
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
]


# ---------------------------------------------------------------------------
# Dispatch table — tool name → callable
# ---------------------------------------------------------------------------


def _propose_set_hostname(new_name: str) -> dict:
    # Validate at propose-time so the chat reply fails fast (HTTP 422 via
    # the planner) instead of creating an action_id that can only error
    # out later at execute time. Same validators the write tool will
    # re-run server-side — defense-in-depth, but the user-facing failure
    # mode is the cheap one.
    _validate_hostname(new_name)

    would_be_commands = [f"hostname {new_name}"]
    running_config = ""
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.warning("propose_set_hostname_precheck_read_failed", error=str(exc))

    # params contains ONLY the executor's kwargs — no propose-time metadata.
    # (`set_hostname(new_name: str, action_id: str)`)
    params: dict[str, Any] = {"new_name": new_name}
    existing = find_existing_block(would_be_commands, running_config) if running_config else None
    preview_meta: dict[str, Any] | None = None
    if existing:
        preview_meta = {
            "existing_entity": existing["anchor"],
            "existing_block": existing["block"],
            "is_exact_match": existing["is_exact_match"],
        }

    action_id = propose_action("set_hostname", params, preview_meta=preview_meta)
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": f"Will run: 'hostname {new_name}' on the C1111",
        "execute_tool": "set_hostname",
        "execute_params": {"new_name": new_name, "action_id": action_id},
        "next_step": _NEXT_STEP_INLINE + " No need to open another screen.",
        "commands": would_be_commands,
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

    would_be_commands = [f"vlan {vlan_id}", f" name {vlan_name}"]
    running_config = ""
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.warning("propose_set_access_vlan_precheck_read_failed", error=str(exc))

    # params contains ONLY the executor's kwargs — no propose-time metadata.
    params: dict[str, Any] = {"vlan_id": vlan_id, "vlan_name": vlan_name}
    existing = find_existing_block(would_be_commands, running_config) if running_config else None
    preview_meta: dict[str, Any] | None = None
    if existing:
        preview_meta = {
            "existing_entity": existing["anchor"],
            "existing_block": existing["block"],
            "is_exact_match": existing["is_exact_match"],
        }

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
        "commands": would_be_commands,
        "preview_meta": preview_meta,
    }


def _propose_webui_set_hostname(new_name: str) -> dict:
    _validate_hostname(new_name)
    # Store under `new_name` to match the flow function's kwarg name
    # (change_hostname_via_webui(new_name, action_id)).
    action_id = propose_action("webui_set_hostname", {"new_name": new_name})
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": f"Will drive WebUI: Administration → Device Properties → set hostname '{new_name}' → Apply",
        "execute_tool": "webui_set_hostname",
        "execute_params": {"new_name": new_name, "action_id": action_id},
        "next_step": _NEXT_STEP_WEBUI,
    }


def _propose_webui_add_access_vlan(vlan_id: int, vlan_name: str) -> dict:
    _validate_vlan_id(vlan_id)
    _validate_vlan_name(vlan_name)
    action_id = propose_action(
        "webui_add_access_vlan", {"vlan_id": vlan_id, "vlan_name": vlan_name}
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


def _propose_webui_configure(**kwargs: Any) -> dict:
    """Propose a generic WebUI configure action.

    Flow: search_docs → webui_open → describe_page → draft_plan → propose_action.
    Returns awaiting_approval with the plan, evidence, and action_id.
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

    # 2. Open WebUI session (no action_id yet — propose_action runs after)
    open_result = webui_open(path=webui_path)
    if "error" in open_result:
        return open_result
    session_id = open_result["session_id"]

    # 3. Fresh describe (the view from webui_open should suffice, but re-describe
    # for the most current snapshot — Angular can paint after initial open).
    desc_result = webui_describe_page(session_id=session_id)
    if "error" in desc_result:
        close_all_sessions()
        return desc_result
    view = desc_result["view"]

    # 3b. Fetch running-config for conflict detection. Soft-fail: if SSH is
    # down or any other error, we still proceed without conflict info.
    running_config = ""
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.warning("propose_webui_configure_running_config_read_failed", error=str(exc))

    # 4. Inner LLM drafts the plan
    try:
        drafted = draft_plan(intent, rag_chunks, view, running_config=running_config)
    except RuntimeError as exc:
        log.error("propose_webui_configure_draft_failed", intent=intent, error=str(exc))
        # Close the orphaned session — propose failed before propose_action
        # took ownership of session_id, so nothing else will clean it up.
        # close_all_sessions is idempotent on missing sessions.
        close_all_sessions()
        return {"error": "draft_failed", "message": str(exc)}

    plan = drafted["plan"]
    verify_text = drafted["verify_text"]
    risk = drafted["risk"]
    equivalent_cli = drafted.get("equivalent_cli_commands") or []

    if not plan:
        # Inner LLM said it can't map the intent. Surface to the planner.
        close_all_sessions()
        return {
            "error": "intent_not_mappable",
            "message": risk,
            "evidence": [
                {"source": c.get("source"), "section": c.get("section")} for c in rag_chunks
            ],
        }

    # 5. Conflict detection using equivalent CLI commands (soft-fail if either
    # side is empty — avoid false positives from an LLM that couldn't infer).
    existing = None
    if equivalent_cli and running_config:
        existing = find_existing_block(equivalent_cli, running_config)

    # 6. Register the action — preview_meta carries conflict fields separately
    # from webui_params so executor's func(**params) never receives unexpected kwargs.
    evidence = [{"source": c.get("source"), "section": c.get("section")} for c in rag_chunks]
    webui_params: dict[str, Any] = {
        "intent": intent,
        "webui_path": webui_path,
        "plan": plan,
        "verify_text": verify_text,
        "risk": risk,
        "evidence": evidence,
        "session_id": session_id,
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
        "plan": plan,
        "verify_text": verify_text,
        "risk": risk,
        "evidence": evidence,
        "step_count": len(plan),
    }

    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "execute_tool": "webui_configure",
        "preview": webui_preview,
        "next_step": _NEXT_STEP_WEBUI,
        "preview_meta": preview_meta,
    }


# Max iterations of the multi-propose loop inside _webui_configure. Failed
# batches count against this budget so failure-recovery stays bounded.
# Bumping this means trusting the inner LLM to converge on more pages — keep
# tight until real flows demand more.
_WEBUI_CONFIGURE_MAX_ITER = 4


def _plan_hash(plan: list[dict[str, Any]]) -> str:
    """Stable hash of a plan for "same plan twice in a row" detection.

    Canonical JSON (sort_keys) so dict key order doesn't fool the equality
    check. SHA-1 is fine here — we're comparing local strings, not signing
    anything.
    """
    return hashlib.sha1(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()


def _act_ok(result: dict[str, Any]) -> bool:
    """A webui_act_by_intent result is considered successful when it has
    ``ok: True`` and no ``error`` key. Mirrors the original Phase 5 check."""
    return result.get("ok") is True and "error" not in result


def _webui_configure(**kwargs: Any) -> dict:
    """Execute a previously-approved webui_configure plan with multi-propose.

    After each batch of steps executes, the page is re-described and the
    inner Haiku is re-invoked with the full execution history. The loop
    continues until ``webui_verify`` returns ``present=True`` OR one of three
    hard-stops fires: iteration cap, inner LLM returns empty plan, or inner
    LLM returns the same plan twice in a row (no progress).

    Failed Playwright steps are NOT terminal — the failure is recorded in
    ``previous_steps`` and the inner LLM gets a chance to adapt on the next
    iteration. Only the hard-stops bail with ``mark_failed``.
    """
    from backend.orchestration.confirmations import get_action, mark_executed, mark_failed

    action_id = kwargs.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        return {"error": "bad_parameters", "message": "action_id must be a non-empty string"}

    # HITL layer 2 — same gate as webui_act
    if not is_approved(action_id):
        return {"error": "not_approved", "message": f"action_id {action_id!r} is not APPROVED"}

    try:
        action = get_action(action_id)
    except KeyError:
        return {"error": "unknown_action", "message": f"no action with id {action_id!r}"}

    params = action.get("params", {})
    plan = params.get("plan", [])
    verify_text = params.get("verify_text")
    session_id = params.get("session_id")
    intent = params.get("intent", "")
    rag_chunks = [
        {"text": "", "source": e.get("source"), "section": e.get("section")}
        for e in params.get("evidence", [])
    ]

    if not plan or not session_id:
        mark_failed(action_id)
        return {"error": "bad_action_params", "message": "action missing plan or session_id"}

    executed_steps: list[dict[str, Any]] = []
    iteration = 0
    last_plan_hash: str | None = _plan_hash(plan)

    while True:
        iteration += 1
        log.info(
            "webui_configure_iteration_started",
            action_id=action_id,
            iteration=iteration,
            prev_steps_count=len(executed_steps),
            step_count=len(plan),
        )
        batch_had_failure = False
        last_failure: dict[str, Any] | None = None

        for idx, step in enumerate(plan):
            intent_dict = {
                "role": step.get("intent", {}).get("role", ""),
                "name": step.get("intent", {}).get("name", ""),
                "action": step.get("action", "click"),
                "value": step.get("value"),
            }
            step_result = webui_act_by_intent(
                session_id=session_id,
                intent=intent_dict,
                action_id=action_id,
            )
            ok = _act_ok(step_result)
            executed_steps.append(
                {
                    "iteration": iteration,
                    "step_index_in_batch": idx,
                    "step": step,
                    "result": step_result,
                    "status": "ok" if ok else "failed",
                }
            )
            if not ok:
                batch_had_failure = True
                last_failure = {
                    "iteration": iteration,
                    "step_index_in_batch": idx,
                    "step": step,
                    "result": step_result,
                }
                log.warning(
                    "webui_configure_step_failed_mid_loop",
                    action_id=action_id,
                    iteration=iteration,
                    step=step,
                    failure=step_result,
                )
                # Stop the batch — feed the failure back to the inner LLM
                # for the next iteration instead of running more steps on
                # what may now be an unexpected page state.
                break

        verify_result: dict[str, Any] | None = None

        # Verify only when the batch ran clean. A failed step leaves the
        # page in an unknown state; a passing verify_text check there
        # could be a false positive (e.g. the prior page still happened
        # to contain the text).
        if not batch_had_failure and verify_text:
            verify_result = webui_verify(session_id=session_id, text=verify_text)
            if verify_result.get("present"):
                mark_executed(action_id)
                close_all_sessions()
                log.info(
                    "webui_configure_iteration_complete",
                    action_id=action_id,
                    iteration=iteration,
                    batch_clean=True,
                    verify_present=True,
                )
                return {
                    "ok": True,
                    "action_id": action_id,
                    "iterations": iteration,
                    "completed_steps": executed_steps,
                    "verify_result": verify_result,
                }
        # NOTE: when verify_text is None and the batch ran clean, we do NOT
        # treat it as terminal — multi-page flows (e.g. static route, OSPF
        # form) routinely propose-time only know step 1 (click Add) and
        # rely on iter 2+ to fill the form. Let the inner LLM decide via
        # an empty plan when the intent is complete.

        # Hard-stop 1: iteration cap
        if iteration >= _WEBUI_CONFIGURE_MAX_ITER:
            mark_failed(action_id)
            close_all_sessions()
            log.error(
                "webui_configure_iteration_cap_hit",
                action_id=action_id,
                intent=intent,
                iterations=iteration,
                executed_count=len(executed_steps),
            )
            return {
                "error": "iteration_cap_hit",
                "iterations": iteration,
                "completed_steps": executed_steps,
                "last_failure": last_failure,
                "verify_result": verify_result,
            }

        # Re-describe regardless of whether the batch failed — a failed
        # click may still have scrolled or partially filled. Fresh view
        # tells the truth.
        new_view_result = webui_describe_page(session_id=session_id)
        if "error" in new_view_result:
            mark_failed(action_id)
            close_all_sessions()
            log.error(
                "webui_configure_describe_failed",
                action_id=action_id,
                iteration=iteration,
                error=new_view_result,
            )
            return {
                "error": "describe_failed",
                "iteration": iteration,
                "describe_error": new_view_result,
                "completed_steps": executed_steps,
            }
        new_view = new_view_result.get("view", {})

        try:
            drafted = draft_plan(
                intent,
                rag_chunks,
                new_view,
                previous_steps=executed_steps,
            )
        except RuntimeError as exc:
            mark_failed(action_id)
            close_all_sessions()
            log.error(
                "webui_configure_inner_draft_failed",
                action_id=action_id,
                iteration=iteration,
                error=str(exc),
            )
            return {
                "error": "inner_draft_failed",
                "iteration": iteration,
                "message": str(exc),
                "completed_steps": executed_steps,
            }

        next_plan = drafted.get("plan") or []
        next_verify_text = drafted.get("verify_text")
        if next_verify_text:
            # Inner LLM may revise verify_text as the flow advances (e.g.
            # narrower expected text once we're past navigation). Honour the
            # update; fall back to original if planner returned None.
            verify_text = next_verify_text

        # Hard-stop 2: inner LLM says give up
        if not next_plan:
            mark_failed(action_id)
            close_all_sessions()
            log.error(
                "webui_configure_inner_plan_empty",
                action_id=action_id,
                iteration=iteration,
                risk=drafted.get("risk"),
            )
            return {
                "error": "inner_plan_empty",
                "iteration": iteration,
                "risk": drafted.get("risk"),
                "completed_steps": executed_steps,
            }

        # Hard-stop 3: identical plan twice in a row → no forward progress
        next_hash = _plan_hash(next_plan)
        if next_hash == last_plan_hash:
            mark_failed(action_id)
            close_all_sessions()
            log.error(
                "webui_configure_inner_plan_stuck",
                action_id=action_id,
                iteration=iteration,
                plan_hash=next_hash,
            )
            return {
                "error": "inner_plan_stuck",
                "iteration": iteration,
                "repeated_plan": next_plan,
                "completed_steps": executed_steps,
            }
        last_plan_hash = next_hash

        log.info(
            "webui_configure_iteration_complete",
            action_id=action_id,
            iteration=iteration,
            batch_clean=not batch_had_failure,
            verify_present=False,
            next_plan_steps=len(next_plan),
        )
        plan = next_plan


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
    # Phase 5 — generic AI-driven WebUI configure (two-step HITL).
    # webui_open / webui_describe_page / webui_verify / webui_act /
    # webui_act_by_intent are internal helpers only (not in TOOL_SCHEMAS).
    "propose_webui_configure": _propose_webui_configure,
    "webui_configure": _webui_configure,
    # CLI AI configure — same propose/execute split. Inner Haiku drafts
    # IOS XE commands grounded in RAG + running-config; denylist filters
    # at propose AND execute time.
    "propose_cli_configure": _propose_cli_configure,
    "cli_configure": _cli_configure,
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
