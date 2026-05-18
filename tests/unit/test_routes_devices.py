"""Tests for GET /api/devices — hardcoded lab device endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
