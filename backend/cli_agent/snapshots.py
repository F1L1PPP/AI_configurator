"""Device snapshot — three show commands saved to disk before/after every write.

Usage:
    pre_dir  = take_snapshot(action_id, "pre")
    # ... send config ...
    post_dir = take_snapshot(action_id, "post")

Files saved per snapshot:
    artifacts/device-snapshots/<action_id>/<phase>/running-config.txt
    artifacts/device-snapshots/<action_id>/<phase>/version.txt
    artifacts/device-snapshots/<action_id>/<phase>/ip-int-brief.txt
"""

from __future__ import annotations

from pathlib import Path

from backend.cli_agent.connection import pool
from backend.core.logging import get_logger
from backend.core.settings import get_settings

log = get_logger(__name__)

_COMMANDS = {
    "running-config": "show running-config",
    "version": "show version",
    "ip-int-brief": "show ip interface brief",
}


def take_snapshot(action_id: str, phase: str = "pre") -> Path:
    """Run three show commands and save outputs to artifacts/device-snapshots/.

    Returns the directory path where the files were written.
    """
    settings = get_settings()
    snap_dir = settings.artifacts_dir / "device-snapshots" / action_id / phase
    snap_dir.mkdir(parents=True, exist_ok=True)

    conn = pool.get_connection(
        settings.router_host,
        settings.router_ssh_user,
        settings.router_ssh_password,
    )

    for filename, command in _COMMANDS.items():
        raw: str = conn.send_command(command, read_timeout=60)
        (snap_dir / f"{filename}.txt").write_text(raw, encoding="utf-8")

    log.info("snapshot_taken", action_id=action_id, phase=phase, path=str(snap_dir))
    return snap_dir
