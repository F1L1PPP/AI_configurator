"""Tool schemas in Anthropic format + dispatcher.

The planner exposes these tools to Claude. The dispatcher maps each tool
name to a Python callable, runs it, and returns a JSON-serializable result.

Design notes:
- Read tools take no parameters and never need approval.
- Write tools require an `action_id` parameter that must already be APPROVED
  in the HITL state machine (defense-in-depth — the write tool itself also
  checks is_approved()).
- Unknown tool names return a structured error instead of raising; this lets
  the planner recover instead of dying mid-conversation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.cli_agent import read_tools, write_tools
from backend.core.logging import get_logger
from backend.orchestration.confirmations import NotApproved, propose_action

log = get_logger(__name__)


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
                "new_name":  {"type": "string"},
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
                "ip":   {"type": "string", "description": "IPv4 address."},
                "mask": {"type": "string", "description": "Subnet mask (dotted)."},
            },
            "required": ["interface", "ip", "mask"],
        },
    },
    {
        "name": "set_interface_ip",
        "description": (
            "Execute a previously approved interface IP assignment. "
            "Requires an APPROVED action_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string"},
                "ip":        {"type": "string"},
                "mask":      {"type": "string"},
                "action_id": {"type": "string"},
            },
            "required": ["interface", "ip", "mask", "action_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatch table — tool name → callable
# ---------------------------------------------------------------------------


def _propose_set_hostname(new_name: str) -> dict:
    action_id = propose_action("set_hostname", {"name": new_name})
    return {
        "status":    "awaiting_approval",
        "action_id": action_id,
        "preview":   f"Will run: 'hostname {new_name}' on the C1111",
        "next_step": (
            f"Open /preview?action_id={action_id} and click APPROVE, "
            "then ask me to execute."
        ),
    }


def _propose_set_interface_ip(interface: str, ip: str, mask: str) -> dict:
    action_id = propose_action(
        "set_interface_ip",
        {"interface": interface, "ip": ip, "mask": mask},
    )
    return {
        "status":    "awaiting_approval",
        "action_id": action_id,
        "preview":   f"Will set {interface} -> {ip}/{mask}",
        "next_step": f"Open /preview?action_id={action_id} and click APPROVE.",
    }


_TOOL_FUNCS: dict[str, Callable[..., Any]] = {
    "show_version":             read_tools.show_version,
    "show_ip_interface_brief":  read_tools.show_ip_interface_brief,
    "show_running_config":      read_tools.show_running_config,
    "show_vlan_brief":          read_tools.show_vlan_brief,
    "propose_set_hostname":     _propose_set_hostname,
    "set_hostname":             write_tools.set_hostname,
    "propose_set_interface_ip": _propose_set_interface_ip,
    "set_interface_ip":         write_tools.set_interface_ip,
}


def execute_tool(name: str, params: dict[str, Any]) -> dict:
    """Invoke a registered tool by name. Always returns a dict (never raises).

    Errors are wrapped into the return value so the planner can surface them
    to the user instead of crashing mid-tool-use-loop.
    """
    if name not in _TOOL_FUNCS:
        log.warning("unknown_tool", tool=name)
        return {"error": f"unknown tool: {name!r}", "available": list(_TOOL_FUNCS)}

    func = _TOOL_FUNCS[name]
    try:
        result = func(**params)
    except NotApproved as exc:
        log.info("tool_not_approved", tool=name, error=str(exc))
        return {"error": "not_approved", "message": str(exc)}
    except TypeError as exc:
        # Wrong arguments — surface but don't crash
        log.warning("tool_bad_params", tool=name, params=params, error=str(exc))
        return {"error": "bad_parameters", "message": str(exc)}
    except Exception as exc:
        log.error("tool_exception", tool=name, error=str(exc))
        return {"error": "tool_failed", "message": str(exc)}

    # Normalize to dict if a tool returns str/list
    if isinstance(result, dict):
        return result
    return {"result": result}


def tool_names() -> list[str]:
    """Return the list of registered tool names."""
    return list(_TOOL_FUNCS)
