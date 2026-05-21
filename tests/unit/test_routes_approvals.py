"""Unit tests for session-lifecycle cleanup in routes_approvals.py (chunk A2).

Verifies that close_all_sessions is called on terminal action transitions:
- /api/execute — always (success, structured error, and unhandled exception).
- /api/reject  — always.

Uses FastAPI TestClient with confirmations state managed via propose/approve
helpers and close_all_sessions mocked to avoid real Playwright calls.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import (
    approve_action,
    propose_action,
)

# _clean_actions fixture is in tests/conftest.py (autouse).


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# execute — close_all_sessions called on all paths
# ---------------------------------------------------------------------------


def test_execute_closes_sessions_on_success(client, monkeypatch):
    """Successful execute → close_all_sessions called once after tool completes."""
    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    approve_action(action_id)

    monkeypatch.setitem(tr._TOOL_FUNCS, "set_hostname", lambda **kw: {"ok": True})

    with patch("backend.api.routes_approvals.close_all_sessions") as mock_close:
        resp = client.post(f"/api/execute/{action_id}")

    assert resp.status_code == 200
    mock_close.assert_called_once()


def test_execute_closes_sessions_on_failure(client, monkeypatch):
    """Tool returns structured error dict → close_all_sessions called even on error path."""
    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    approve_action(action_id)

    monkeypatch.setitem(
        tr._TOOL_FUNCS,
        "set_hostname",
        lambda **kw: {"error": "tool_failed", "message": "boom"},
    )

    with patch("backend.api.routes_approvals.close_all_sessions") as mock_close:
        resp = client.post(f"/api/execute/{action_id}")

    assert resp.status_code == 500
    mock_close.assert_called_once()


def test_execute_closes_sessions_on_wrong_state(client):
    """execute called with PROPOSED state (not APPROVED) → 409 but still closes sessions."""
    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    # Do NOT approve — action is still PROPOSED.

    with patch("backend.api.routes_approvals.close_all_sessions") as mock_close:
        resp = client.post(f"/api/execute/{action_id}")

    assert resp.status_code == 409
    mock_close.assert_called_once()


# ---------------------------------------------------------------------------
# reject — close_all_sessions called
# ---------------------------------------------------------------------------


def test_reject_closes_sessions(client):
    """reject called on a PROPOSED action → closes sessions after state transition."""
    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})

    with patch("backend.api.routes_approvals.close_all_sessions") as mock_close:
        resp = client.post(f"/api/reject/{action_id}")

    assert resp.status_code == 200
    mock_close.assert_called_once()


def test_reject_closes_sessions_on_wrong_state(client):
    """reject called on an already-rejected action → 409 but still closes sessions."""
    from backend.orchestration.confirmations import reject_action

    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    reject_action(action_id)  # first reject — transitions to REJECTED

    with patch("backend.api.routes_approvals.close_all_sessions") as mock_close:
        resp = client.post(f"/api/reject/{action_id}")  # second reject — wrong state

    assert resp.status_code == 409
    mock_close.assert_called_once()
