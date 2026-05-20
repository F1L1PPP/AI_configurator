"""Unit tests for GET /api/suggestions — context-aware suggestion chips.

Mocks SSH (show_running_config) and Haiku (_call_haiku) so no real
network calls are made.  Uses TestClient fixture pattern from
tests/unit/test_routes_devices.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.api.routes_suggestions as routes_suggestions
from backend.api.routes_suggestions import _DEFAULT_SUGGESTIONS, _build_digest
from backend.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_RUNNING_CONFIG = """\
hostname LAB-R5
!
vlan internal allocation policy ascending
!
vlan 1
 name default
!
vlan 30
 name OFFICE
!
vlan 40
!
interface Vlan1
 ip address 192.168.10.1 255.255.255.0
!
interface Loopback0
 ip address 5.5.5.5 255.255.255.255
!
interface GigabitEthernet0/0/0
!
end
"""

_FAKE_CHIPS = [
    "add VLAN 50 named GUEST",
    "change hostname to LAB-R6",
    "show ip interface brief",
    "how do I configure an SVI?",
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset the in-memory suggestions cache before each test so state
    does not leak across tests."""
    routes_suggestions._cache.clear()
    yield
    routes_suggestions._cache.clear()


# ---------------------------------------------------------------------------
# Test 1 — basic happy path
# ---------------------------------------------------------------------------


def test_endpoint_returns_200_with_suggestions_key_and_source_key(client, monkeypatch):
    """Happy path: SSH succeeds, Haiku returns chips → HTTP 200 with
    expected shape and source == 'fresh'."""
    monkeypatch.setattr(
        routes_suggestions.read_tools,
        "show_running_config",
        lambda: _SAMPLE_RUNNING_CONFIG,
    )
    monkeypatch.setattr(routes_suggestions, "_call_haiku", lambda digest: _FAKE_CHIPS)

    resp = client.get("/api/suggestions")

    assert resp.status_code == 200
    body = resp.json()
    assert "suggestions" in body
    assert "source" in body
    assert isinstance(body["suggestions"], list)
    assert len(body["suggestions"]) == 4
    assert body["source"] == "fresh"


# ---------------------------------------------------------------------------
# Test 2 — SSH failure → fallback
# ---------------------------------------------------------------------------


def test_suggestions_falls_back_when_show_running_config_raises(client, monkeypatch):
    """When show_running_config raises (SSH unreachable), the endpoint must
    return _DEFAULT_SUGGESTIONS and source == 'fallback' — no HTTP error."""

    def boom():
        raise RuntimeError("ssh handshake failed")

    monkeypatch.setattr(routes_suggestions.read_tools, "show_running_config", boom)

    resp = client.get("/api/suggestions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestions"] == _DEFAULT_SUGGESTIONS
    assert body["source"] == "fallback"


# ---------------------------------------------------------------------------
# Test 3 — Haiku overloaded → fallback
# ---------------------------------------------------------------------------


def test_suggestions_falls_back_when_haiku_overloaded(client, monkeypatch):
    """When _call_haiku raises AnthropicOverloadedError, the endpoint must
    return _DEFAULT_SUGGESTIONS and source == 'fallback'."""
    import httpx
    from anthropic._exceptions import OverloadedError as AnthropicOverloadedError

    monkeypatch.setattr(
        routes_suggestions.read_tools,
        "show_running_config",
        lambda: _SAMPLE_RUNNING_CONFIG,
    )

    def raise_overloaded(digest: str):
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_response = httpx.Response(
            status_code=529,
            headers={"request-id": "req_test_overloaded_529"},
            request=mock_request,
        )
        raise AnthropicOverloadedError(
            message="Overloaded",
            response=mock_response,
            body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
        )

    monkeypatch.setattr(routes_suggestions, "_call_haiku", raise_overloaded)

    resp = client.get("/api/suggestions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestions"] == _DEFAULT_SUGGESTIONS
    assert body["source"] == "fallback"


# ---------------------------------------------------------------------------
# Test 4 — 30 s cache: second call within TTL must NOT re-invoke Haiku
# ---------------------------------------------------------------------------


def test_suggestions_caches_for_30s(client, monkeypatch):
    """First call populates cache; second call within TTL returns 'cache'
    source without re-calling Haiku.  Call counter asserts exactly 1 Haiku
    invocation across two HTTP requests."""
    monkeypatch.setattr(
        routes_suggestions.read_tools,
        "show_running_config",
        lambda: _SAMPLE_RUNNING_CONFIG,
    )

    call_count = {"n": 0}

    def counting_haiku(digest: str) -> list[str]:
        call_count["n"] += 1
        return _FAKE_CHIPS

    monkeypatch.setattr(routes_suggestions, "_call_haiku", counting_haiku)

    # First request — cache miss, Haiku called once.
    resp1 = client.get("/api/suggestions?device_id=test-cache-device")
    assert resp1.status_code == 200
    assert resp1.json()["source"] == "fresh"

    # Second request within TTL — should hit cache, NOT call Haiku again.
    resp2 = client.get("/api/suggestions?device_id=test-cache-device")
    assert resp2.status_code == 200
    assert resp2.json()["source"] == "cache"

    assert call_count["n"] == 1, f"Expected 1 Haiku call, got {call_count['n']}"


# ---------------------------------------------------------------------------
# Test 5 — _build_digest pure unit test
# ---------------------------------------------------------------------------


def test_digest_extracts_hostname_vlans_and_interface_ips():
    """_build_digest must extract hostname, VLAN stanzas, and interface IPs
    from a running-config string into the compact one-line-per-entity format."""
    digest = _build_digest(_SAMPLE_RUNNING_CONFIG)

    # hostname
    assert "hostname LAB-R5" in digest

    # VLANs with names
    assert "vlan 1 default" in digest
    assert "vlan 30 OFFICE" in digest

    # VLAN without name (VLAN 40)
    assert "vlan 40" in digest

    # Interfaces with IPs
    assert "interface Vlan1 192.168.10.1 255.255.255.0" in digest
    assert "interface Loopback0 5.5.5.5 255.255.255.255" in digest

    # Interface without ip address — should still appear
    assert "interface GigabitEthernet0/0/0" in digest

    # Internal allocation policy line must NOT appear
    assert "internal allocation policy" not in digest


# ---------------------------------------------------------------------------
# Test 6 — Haiku emits nothing → fallback
# ---------------------------------------------------------------------------


def test_suggestions_returns_defaults_when_haiku_emits_nothing(client, monkeypatch):
    """When _call_haiku returns an empty list (LLM replied with empty text),
    the endpoint must return _DEFAULT_SUGGESTIONS and source == 'fallback'."""
    monkeypatch.setattr(
        routes_suggestions.read_tools,
        "show_running_config",
        lambda: _SAMPLE_RUNNING_CONFIG,
    )
    monkeypatch.setattr(routes_suggestions, "_call_haiku", lambda digest: [])

    resp = client.get("/api/suggestions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestions"] == _DEFAULT_SUGGESTIONS
    assert body["source"] == "fallback"
