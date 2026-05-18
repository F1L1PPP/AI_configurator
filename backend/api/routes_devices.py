from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["devices"])

_DEVICES: list[dict[str, Any]] = [
    {
        "id": "router-01",
        "name": "C1111-LAB",
        "ip": "192.168.10.1",
        "model": "Cisco C1111-4P",
        "ios": "IOS XE 17.6.3a",
        "status": "connected",
        "health": "good",
        "uptime": "—",  # not wired to show version yet; static for Phase 1
        "lastSeen": "now",
    },
]


@router.get("/devices")
async def list_devices() -> list[dict[str, Any]]:
    return _DEVICES
