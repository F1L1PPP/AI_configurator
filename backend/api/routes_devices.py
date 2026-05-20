from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from backend.cli_agent import read_tools
from backend.core.logging import get_logger
from backend.core.settings import get_settings

router = APIRouter(prefix="/api", tags=["devices"])
log = get_logger(__name__)


# Static device card fields that aren't on the router itself (id/name/model
# are deployment-side labels; status/health/lastSeen are agent-side state).
# The dynamic fields (ios, uptime, ip) are populated by `_enrich_with_show_version`
# below, with the static values as fallback when SSH is unavailable.
_STATIC_DEVICE: dict[str, Any] = {
    "id": "router-01",
    "name": "C1111-LAB",
    "ip": "192.168.10.1",
    "model": "Cisco C1111-4P",
    "ios": "IOS XE 17.6.3a",
    "status": "connected",
    "health": "good",
    "uptime": "—",
    "lastSeen": "now",
}


def _enrich_with_show_version(device: dict[str, Any]) -> dict[str, Any]:
    """Populate `ios`, `uptime`, `ip` from real device state.

    `ip` comes from `settings.router_host` — that's the address we actually
    connect to, so it's authoritative. `ios` and `uptime` come from a single
    `show version` parse. SSH error or missing fields fall back to the static
    values silently so the Dashboard never breaks because the router is
    momentarily unreachable.
    """
    enriched = dict(device)
    try:
        host = get_settings().router_host
        if isinstance(host, str) and host.strip():
            enriched["ip"] = host
    except Exception as exc:
        log.warning("devices_settings_read_failed", error=str(exc))

    try:
        parsed = read_tools.show_version()
    except Exception as exc:
        log.warning("devices_show_version_failed", error=str(exc))
        return enriched

    version = parsed.get("version") if isinstance(parsed, dict) else None
    uptime = parsed.get("uptime") if isinstance(parsed, dict) else None
    hostname = parsed.get("hostname") if isinstance(parsed, dict) else None
    if isinstance(version, str) and version.strip():
        enriched["ios"] = f"IOS XE {version}" if not version.lower().startswith("ios") else version
    if isinstance(uptime, str) and uptime.strip():
        enriched["uptime"] = uptime
    if isinstance(hostname, str) and hostname.strip():
        enriched["name"] = hostname
    return enriched


@router.get("/devices")
async def list_devices() -> list[dict[str, Any]]:
    return [_enrich_with_show_version(_STATIC_DEVICE)]


@router.get("/devices/{device_id}/last-backup")
async def get_last_backup(device_id: str) -> dict[str, Any]:
    """Most-recent `post/` snapshot for the device + total snapshot count
    (used by the Dashboard "Last backup" widget AND the "Configs saved" KPI).

    A `post` snapshot is taken after every write attempt (success or failure
    — see write_tools.py); it represents the freshest captured copy of the
    device config. Returns `{action_id, taken_at, snapshot_path, count}` or
    `None`-filled fields plus `count: 0` when no snapshots exist yet
    (frontend renders "—").

    `device_id` is accepted for future multi-device support but unused
    today — there's one router and one snapshot tree.
    """
    snap_root = get_settings().artifacts_dir / "device-snapshots"
    empty = {
        "device_id": device_id,
        "action_id": None,
        "taken_at": None,
        "snapshot_path": None,
        "count": 0,
    }
    if not snap_root.exists():
        return empty

    post_dirs = [p for p in snap_root.glob("*/post") if p.is_dir()]
    if not post_dirs:
        return empty

    latest = max(post_dirs, key=lambda p: p.stat().st_mtime)
    return {
        "device_id": device_id,
        "action_id": latest.parent.name,
        "taken_at": datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC).isoformat(),
        "snapshot_path": str(latest),
        "count": len(post_dirs),
    }
