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

from collections.abc import Callable
from typing import Any

from backend.cli_agent import read_tools, write_tools
from backend.cli_agent.write_tools import (
    _validate_hostname,
    _validate_interface,
    _validate_interface_ip_and_mask,
    _validate_vlan_id,
    _validate_vlan_name,
)
from backend.core.logging import get_logger
from backend.orchestration.confirmations import (
    NotApproved,
    is_approved,
    propose_action,
)
from backend.webui_agent.flows.add_access_vlan import add_access_vlan_via_webui
from backend.webui_agent.flows.change_hostname import change_hostname_via_webui

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
            "answer in real Cisco docs. Read-only."
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
                    "description": "Max number of chunks to return (default 5).",
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
    # Store the param under the same key the write tool's signature expects
    # (`set_hostname(new_name: str, action_id: str)`), so the new
    # `/api/execute/{action_id}` endpoint can dispatch via {**params,
    # action_id=...} without a name-translation step.
    action_id = propose_action("set_hostname", {"new_name": new_name})
    return {
        "status": "awaiting_approval",
        "action_id": action_id,
        "preview": f"Will run: 'hostname {new_name}' on the C1111",
        "execute_tool": "set_hostname",
        "execute_params": {"new_name": new_name, "action_id": action_id},
        "next_step": _NEXT_STEP_INLINE + " No need to open another screen.",
    }


def _propose_set_interface_ip(interface: str, ip: str, mask: str) -> dict:
    _validate_interface(interface)
    _validate_interface_ip_and_mask(ip, mask)
    action_id = propose_action(
        "set_interface_ip",
        {"interface": interface, "ip": ip, "mask": mask},
    )
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
    }


def _propose_set_access_vlan(vlan_id: int, vlan_name: str) -> dict:
    _validate_vlan_id(vlan_id)
    _validate_vlan_name(vlan_name)
    action_id = propose_action("set_access_vlan", {"vlan_id": vlan_id, "vlan_name": vlan_name})
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
