"""Write tools for Cisco IOS XE devices.

Rules (hard — do not relax):
- Every function requires an approved action_id. NotApproved is raised
  immediately if the gate hasn't been cleared; the device is never touched.
- Pre-snapshot fires before the first config command. Post-snapshot fires
  after success. Both land in artifacts/device-snapshots/<action_id>/.
- On any error during the config push: log, mark the action FAILED, re-raise.
  Never auto-retry a write.
"""

from __future__ import annotations

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


def _get_conn():
    s = get_settings()
    return pool.get_connection(s.router_host, s.router_ssh_user, s.router_ssh_password)


def _guard(action_id: str) -> None:
    if not is_approved(action_id):
        raise NotApproved(
            f"action_id {action_id!r} has not been approved. "
            "Call POST /api/approve/{action_id} first."
        )


def set_hostname(new_name: str, action_id: str) -> dict:
    """Rename the router hostname.

    Requires prior approval. Takes pre+post snapshots around the change.
    Returns a structured result dict with snapshot paths and raw output.
    """
    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    try:
        conn = _get_conn()
        output: str = conn.send_config_set([f"hostname {new_name}"])
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
    """
    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    try:
        conn = _get_conn()
        output: str = conn.send_config_set([
            f"interface {interface}",
            f" ip address {ip} {mask}",
            " no shutdown",
        ])
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
