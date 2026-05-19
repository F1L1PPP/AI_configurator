"""Read-only CLI tools for Cisco IOS XE devices.

Each function: opens (or reuses) the SSH connection, runs one show command,
parses the output through ntc-templates, logs the action to actions.log, and
returns a typed result.

These are safe to call at any time — they never modify device configuration.
"""

from __future__ import annotations

import time
from typing import Any

from backend.cli_agent.connection import pool
from backend.cli_agent.parsers import parse
from backend.core.logging import get_logger
from backend.core.settings import get_settings

log = get_logger(__name__)

_PLATFORM = "cisco_ios"


def _get_conn() -> Any:
    s = get_settings()
    return pool.get_connection(s.router_host, s.router_ssh_user, s.router_ssh_password)


def _run(command: str, use_textfsm: bool = True) -> list[dict] | str:
    conn = _get_conn()
    raw: str = conn.send_command(command, read_timeout=60)
    if use_textfsm:
        return parse(_PLATFORM, command, raw)
    return raw


def _log_action(
    tool: str,
    params: dict,
    result: Any,
    duration_ms: int,
) -> None:
    summary: str
    if isinstance(result, list):
        summary = f"{len(result)} rows"
    elif isinstance(result, str):
        summary = f"{len(result)} chars"
    else:
        summary = repr(result)[:120]

    log.info(
        "tool_call",
        tool=tool,
        params=params,
        result_summary=summary,
        duration_ms=duration_ms,
    )


def show_version() -> dict:
    """Run `show version` and return the first parsed row as a dict."""
    t0 = time.monotonic()
    result = _run("show version")
    ms = int((time.monotonic() - t0) * 1000)
    parsed = result[0] if isinstance(result, list) and result else {"raw": str(result)[:500]}
    _log_action("show_version", {}, parsed, ms)
    return parsed


def show_ip_interface_brief() -> list[dict]:
    """Run `show ip interface brief` and return a list of interface dicts."""
    t0 = time.monotonic()
    result = _run("show ip interface brief")
    ms = int((time.monotonic() - t0) * 1000)
    parsed: list[dict] = result if isinstance(result, list) else []
    _log_action("show_ip_interface_brief", {}, parsed, ms)
    return parsed


def show_running_config() -> str:
    """Run `show running-config` and return the raw config string.

    TextFSM is intentionally skipped — running-config is free-form text
    that no template can fully cover, and callers need the raw output.
    """
    t0 = time.monotonic()
    raw = _run("show running-config", use_textfsm=False)
    ms = int((time.monotonic() - t0) * 1000)
    _log_action("show_running_config", {}, raw, ms)
    return str(raw)


def show_vlan_brief() -> list[dict]:
    """Run `show vlan brief` and return a list of VLAN dicts."""
    t0 = time.monotonic()
    result = _run("show vlan brief")
    ms = int((time.monotonic() - t0) * 1000)
    parsed: list[dict] = result if isinstance(result, list) else []
    _log_action("show_vlan_brief", {}, parsed, ms)
    return parsed


def show_running_config_interface(interface: str) -> str:
    """Run `show running-config interface <name>` and return raw text.

    Caller is responsible for validating `interface` against the IOS
    grammar — this helper interpolates it verbatim into the command.
    Used by propose-time hardware pre-checks (e.g. detect that a target
    port is a hardware switchport before proposing `ip address` on it).
    """
    t0 = time.monotonic()
    raw = _run(f"show running-config interface {interface}", use_textfsm=False)
    ms = int((time.monotonic() - t0) * 1000)
    _log_action("show_running_config_interface", {"interface": interface}, raw, ms)
    return str(raw)
