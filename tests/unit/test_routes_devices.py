"""Tests for GET /api/devices — lab device endpoint with show_version enrichment."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.api.routes_devices as routes_devices
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


_EXPECTED_KEYS = {"id", "name", "ip", "model", "ios", "status", "health", "uptime", "lastSeen"}


def test_get_devices_returns_200(client):
    resp = client.get("/api/devices")
    assert resp.status_code == 200


def test_get_devices_returns_expected_shape(client):
    resp = client.get("/api/devices")
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    device = data[0]
    assert device["id"] == "router-01"
    assert device["ip"] == "192.168.10.1"
    assert set(device.keys()) == _EXPECTED_KEYS


def test_get_devices_enriches_ios_and_uptime_from_show_version(client, monkeypatch):
    """When show_version is reachable, ios + uptime come from parsed output —
    not the static fallback. Dashboard widgets read these fields."""

    def fake_show_version() -> dict:
        return {
            "version": "17.06.03a",
            "uptime": "1 day, 2 hours, 17 minutes",
            "hostname": "c1111-lab",
        }

    monkeypatch.setattr(routes_devices.read_tools, "show_version", fake_show_version)

    resp = client.get("/api/devices")
    device = resp.json()[0]
    assert device["ios"] == "IOS XE 17.06.03a"
    assert device["uptime"] == "1 day, 2 hours, 17 minutes"


def test_get_devices_falls_back_when_show_version_raises(client, monkeypatch):
    """SSH failure (router unreachable) must NOT break the Dashboard — fall
    back to the static device card silently. Existing keys remain populated."""

    def boom() -> dict:
        raise RuntimeError("ssh handshake failed")

    monkeypatch.setattr(routes_devices.read_tools, "show_version", boom)

    resp = client.get("/api/devices")
    assert resp.status_code == 200
    device = resp.json()[0]
    assert device["ios"] == "IOS XE 17.6.3a"  # static fallback
    assert device["uptime"] == "—"  # static fallback
    assert set(device.keys()) == _EXPECTED_KEYS


# ---------------------------------------------------------------------------
# GET /api/devices/{device_id}/last-backup
# ---------------------------------------------------------------------------


def _make_snapshot(root, action_id: str, phase: str, mtime: float | None = None) -> None:
    """Create artifacts/device-snapshots/<action_id>/<phase>/ with one file
    and optionally stamp its mtime so we can order multiple snapshots."""
    import os

    d = root / "device-snapshots" / action_id / phase
    d.mkdir(parents=True, exist_ok=True)
    (d / "running-config.txt").write_text("hostname test\n", encoding="utf-8")
    if mtime is not None:
        os.utime(d, (mtime, mtime))


def test_last_backup_returns_empty_fields_when_no_snapshots(client, tmp_path, monkeypatch):
    """Fresh install (no snapshots dir) -> all None + count 0, no 404."""
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_devices, "get_settings", lambda: fake_settings)

    resp = client.get("/api/devices/router-01/last-backup")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "device_id": "router-01",
        "action_id": None,
        "taken_at": None,
        "snapshot_path": None,
        "count": 0,
    }


def test_last_backup_picks_most_recent_post_and_reports_count(client, tmp_path, monkeypatch):
    """Multiple post snapshots -> highest-mtime wins; count is the total
    `post/` dirs across all action_ids (drives the 'Configs saved' KPI)."""
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_devices, "get_settings", lambda: fake_settings)

    _make_snapshot(tmp_path, "act_oldest", "post", mtime=1_700_000_000.0)
    _make_snapshot(tmp_path, "act_newest", "post", mtime=1_700_000_500.0)
    _make_snapshot(tmp_path, "act_middle", "post", mtime=1_700_000_200.0)

    resp = client.get("/api/devices/router-01/last-backup")
    body = resp.json()
    assert body["action_id"] == "act_newest"
    assert body["device_id"] == "router-01"
    assert body["taken_at"].startswith("20")  # ISO 8601 starts with year
    assert "act_newest" in body["snapshot_path"]
    assert body["count"] == 3


def test_last_backup_ignores_pre_only_snapshots(client, tmp_path, monkeypatch):
    """Pre-only snapshots (never had a post written) shouldn't count as
    a backup — caller's contract is 'last successful copy of config'."""
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_devices, "get_settings", lambda: fake_settings)

    _make_snapshot(tmp_path, "act_pre_only", "pre", mtime=1_700_000_000.0)

    resp = client.get("/api/devices/router-01/last-backup")
    body = resp.json()
    assert body["action_id"] is None
    assert body["snapshot_path"] is None
