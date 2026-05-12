"""Write tools for Cisco IOS XE devices.

Rules (hard — do not relax):
- Every function requires an approved action_id. NotApproved is raised
  immediately if the gate hasn't been cleared; the device is never touched.
- Every user-supplied parameter is validated against a strict grammar
  BEFORE it reaches Netmiko. Cisco IOS swallows newlines as command
  separators, so unvalidated input is a command-injection vector.
- Every send_config_set call sets read_timeout so a hung SSH read can't
  pin a FastAPI worker forever.
- Pre-snapshot fires before the first config command. Post-snapshot fires
  after success. Both land in artifacts/device-snapshots/<action_id>/.
- On any error during the config push: log, mark the action FAILED, re-raise.
  Never auto-retry a write.
"""

from __future__ import annotations

import ipaddress
import re
import time
from pathlib import Path

from backend.cli_agent.connection import pool
from backend.cli_agent.snapshots import take_snapshot
from backend.core.logging import get_logger
from backend.core.settings import get_settings
from backend.orchestration.confirmations import (
    NotApproved,
    is_approved,
    mark_executed,
    mark_failed,
)

log = get_logger(__name__)

# Timeout for any single send_config_set call. Long enough for snapshot-sized
# configs, short enough that a hung SSH session doesn't pin a FastAPI worker
# indefinitely.
CONFIG_READ_TIMEOUT_S = 30

# Cisco IOS hostname grammar: 1-63 chars, must start with letter, then
# letters/digits/hyphens. Rejects newlines, spaces, semicolons — the things
# that turn into command injection.
_HOSTNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,62}$")

# Cisco interface name grammar: letter, then letters/digits/`./:-`.
# Covers GigabitEthernet0/0/1, Vlan10, FastEthernet1/0/2, etc.
_INTERFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9/.:\-]{1,30}$")


# ---------------------------------------------------------------------------
# Validators — raise ValueError on bad input; never reach Netmiko
# ---------------------------------------------------------------------------


def _validate_hostname(name: str) -> None:
    if not isinstance(name, str) or not _HOSTNAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid hostname {name!r}: must be 1-63 chars, start with a "
            "letter, contain only letters/digits/hyphens (RFC 1035 + Cisco)"
        )


def _validate_interface(interface: str) -> None:
    if not isinstance(interface, str) or not _INTERFACE_RE.fullmatch(interface):
        raise ValueError(
            f"invalid interface name {interface!r}: must match "
            "letter + letters/digits/./:/- (e.g. 'GigabitEthernet0/0/1')"
        )


def _validate_ipv4(value: str, kind: str) -> None:
    """Validate an IPv4 dotted-quad. `kind` is 'address' or 'mask' for the
    error message — both use the same ipaddress.IPv4Address check; the mask
    can be any valid IPv4 (Cisco supports non-contiguous masks for ACLs)."""
    try:
        ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise ValueError(
            f"invalid IPv4 {kind} {value!r}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _get_conn():
    s = get_settings()
    return pool.get_connection(s.router_host, s.router_ssh_user, s.router_ssh_password)


def _guard(action_id: str) -> None:
    if not is_approved(action_id):
        raise NotApproved(
            f"action_id {action_id!r} has not been approved. "
            "Call POST /api/approve/{action_id} first."
        )


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


def set_hostname(new_name: str, action_id: str) -> dict:
    """Rename the router hostname.

    Requires prior approval. Takes pre+post snapshots around the change.
    Returns a structured result dict with snapshot paths and raw output.

    Raises ValueError on invalid hostname BEFORE any router contact.
    """
    _validate_hostname(new_name)   # before the approval check — fail-fast
    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    try:
        conn = _get_conn()
        output: str = conn.send_config_set(
            [f"hostname {new_name}"],
            read_timeout=CONFIG_READ_TIMEOUT_S,
        )
        ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="set_hostname",
            action_id=action_id,
            error=str(exc),
        )
        raise  # never auto-retry

    # Hostname change alters the router prompt. Invalidate the pooled
    # connection so the next call reconnects and detects the new prompt.
    s = get_settings()
    pool.invalidate(s.router_host, s.router_ssh_user)

    post_dir: Path = take_snapshot(action_id, "post")
    mark_executed(action_id)

    log.info(
        "tool_call",
        tool="set_hostname",
        params={"name": new_name, "action_id": action_id},
        result_summary=f"hostname set to {new_name!r}",
        duration_ms=ms,
    )

    return {
        "tool":          "set_hostname",
        "params":        {"name": new_name},
        "output":        output,
        "snapshot_pre":  str(pre_dir),
        "snapshot_post": str(post_dir),
        "duration_ms":   ms,
    }


def set_interface_ip(
    interface: str,
    ip: str,
    mask: str,
    action_id: str,
) -> dict:
    """Assign an IP address to an interface and bring it up.

    Example:
        set_interface_ip("GigabitEthernet0/0/0", "10.0.0.1", "255.255.255.0", action_id)

    Requires prior approval. Takes pre+post snapshots.
    Raises ValueError on invalid interface/ip/mask BEFORE any router contact.
    """
    _validate_interface(interface)
    _validate_ipv4(ip,   "address")
    _validate_ipv4(mask, "mask")
    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    try:
        conn = _get_conn()
        output: str = conn.send_config_set(
            [
                f"interface {interface}",
                f" ip address {ip} {mask}",
                " no shutdown",
            ],
            read_timeout=CONFIG_READ_TIMEOUT_S,
        )
        ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="set_interface_ip",
            action_id=action_id,
            error=str(exc),
        )
        raise

    post_dir: Path = take_snapshot(action_id, "post")
    mark_executed(action_id)

    log.info(
        "tool_call",
        tool="set_interface_ip",
        params={"interface": interface, "ip": ip, "mask": mask, "action_id": action_id},
        result_summary=f"{interface} → {ip}/{mask}",
        duration_ms=ms,
    )

    return {
        "tool":          "set_interface_ip",
        "params":        {"interface": interface, "ip": ip, "mask": mask},
        "output":        output,
        "snapshot_pre":  str(pre_dir),
        "snapshot_post": str(post_dir),
        "duration_ms":   ms,
    }
