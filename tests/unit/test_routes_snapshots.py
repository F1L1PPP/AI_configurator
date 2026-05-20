"""Tests for GET /api/actions/{action_id}/snapshot/{phase} endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.api.routes_snapshots as routes_snapshots
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _write_snapshot(root, action_id: str, phase: str, content: str = "hostname lab\n") -> None:
    """Create artifacts/device-snapshots/<action_id>/<phase>/running-config.txt."""
    d = root / "device-snapshots" / action_id / phase
    d.mkdir(parents=True, exist_ok=True)
    (d / "running-config.txt").write_text(content, encoding="utf-8")


def test_snapshot_returns_running_config_when_file_exists(client, tmp_path, monkeypatch):
    """200 with correct body when the snapshot file exists on disk."""
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_snapshots, "get_settings", lambda: fake_settings)

    expected = "hostname act-test-router\ninterface GigabitEthernet0/0\n"
    _write_snapshot(tmp_path, "act_test_123", "pre", content=expected)

    resp = client.get("/api/actions/act_test_123/snapshot/pre")
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_id"] == "act_test_123"
    assert body["phase"] == "pre"
    assert body["running_config"] == expected


def test_snapshot_404_when_action_dir_missing(client, tmp_path, monkeypatch):
    """404 snapshot_not_found when no snapshot directory exists for that action."""
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_snapshots, "get_settings", lambda: fake_settings)

    # Nothing written — tmp_path has no device-snapshots subdirectory at all.
    resp = client.get("/api/actions/act_nonexistent_abc/snapshot/pre")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "snapshot_not_found"


def test_snapshot_404_when_running_config_file_missing(client, tmp_path, monkeypatch):
    """404 when the phase dir exists but running-config.txt is absent."""
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_snapshots, "get_settings", lambda: fake_settings)

    # Create the phase dir with a different file only — no running-config.txt.
    phase_dir = tmp_path / "device-snapshots" / "act_test_456" / "pre"
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / "version.txt").write_text("IOS XE 17.6.3a\n", encoding="utf-8")

    resp = client.get("/api/actions/act_test_456/snapshot/pre")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "snapshot_not_found"


def test_snapshot_422_on_invalid_phase(client, tmp_path, monkeypatch):
    """422 when phase is not 'pre' or 'post'."""
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_snapshots, "get_settings", lambda: fake_settings)

    resp = client.get("/api/actions/act_test_789/snapshot/invalid")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "phase must be 'pre' or 'post'"


def test_snapshot_422_on_path_traversal_action_id(client, tmp_path, monkeypatch):
    """422 when action_id contains path-traversal characters.

    FastAPI URL-decodes %2F before routing, so '../escape' may become a
    separate path segment. We test both a plain traversal string and an
    action_id that doesn't match the regex guard.
    """
    fake_settings = type("S", (), {"artifacts_dir": tmp_path})()
    monkeypatch.setattr(routes_snapshots, "get_settings", lambda: fake_settings)

    # Plain traversal string that passes URL routing but fails the regex.
    resp = client.get("/api/actions/..%2Fescape/snapshot/pre")
    # FastAPI may return 404 from router normalization OR 422 from our guard —
    # either is acceptable because no filesystem path was constructed.
    assert resp.status_code in (404, 422)

    # Unambiguous regex-rejection: action_id with slash characters or spaces.
    resp2 = client.get("/api/actions/act_bad%20id/snapshot/pre")
    assert resp2.status_code == 422
    assert resp2.json()["detail"] == "invalid action_id format"
