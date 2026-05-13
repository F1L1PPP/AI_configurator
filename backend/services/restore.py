"""Config restore from a pre-snapshot.

Rollback path only — never called automatically. The operator triggers this
explicitly after reviewing the pre-snapshot file.

Approach: read running-config.txt from the snapshot, strip IOS headers and
comment lines, send the remaining config commands via Netmiko send_config_set.
This is sufficient for Day 3 (hostname restore). SCP-based full restore is
a Day 4+ enhancement if needed.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from backend.cli_agent.connection import pool
from backend.core.logging import get_logger
from backend.core.settings import get_settings

log = get_logger(__name__)

# Lines that are IOS XE headers/comments, not config commands
_SKIP_PREFIXES = (
    "!",
    "Building configuration",
    "Current configuration",
    "Last configuration change",
    "NVRAM config last updated",
)


def _extract_config_lines(raw: str) -> list[str]:
    lines = []
    for line in raw.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        if any(stripped.lstrip().startswith(p) for p in _SKIP_PREFIXES):
            continue
        if stripped.strip() == "end":
            continue
        lines.append(stripped)
    return lines


def restore_config(snapshot_path: str | Path) -> dict:
    """Restore the running-config from a snapshot directory.

    Args:
        snapshot_path: path to a snapshot phase directory, e.g.
                       artifacts/device-snapshots/<action_id>/pre/

    Returns:
        dict with 'restored_from' and truncated 'output'.
    """
    snap_dir = Path(snapshot_path)
    config_file = snap_dir / "running-config.txt"

    if not config_file.exists():
        raise FileNotFoundError(f"No running-config.txt in {snap_dir}")

    raw = config_file.read_text(encoding="utf-8")
    config_lines = _extract_config_lines(raw)

    if not config_lines:
        raise ValueError(f"No usable config lines found in {config_file}")

    s = get_settings()
    conn = pool.get_connection(s.router_host, s.router_ssh_user, s.router_ssh_password)

    # Re-detect the live prompt before sending config. This is required when
    # the router hostname changed since the connection was created — the cached
    # base_prompt would be stale and config_mode() would time out waiting for
    # the old hostname pattern.
    with contextlib.suppress(Exception):
        conn.find_prompt()

    log.info(
        "restore_started",
        snapshot=str(snap_dir),
        lines=len(config_lines),
    )

    try:
        output: str = conn.send_config_set(config_lines, read_timeout=120)
    except Exception:
        # If send_config_set fails mid-flight the connection may be stuck in
        # config mode. Try to exit cleanly; if that also fails, invalidate the
        # pooled connection so the next caller gets a fresh one.
        try:
            conn.exit_config_mode()
        except Exception:
            pool.invalidate(s.router_host, s.router_ssh_user)
        raise

    log.info("restore_complete", snapshot=str(snap_dir))

    return {
        "restored_from": str(snap_dir),
        "lines_sent": len(config_lines),
        "output": output[:500],
    }
