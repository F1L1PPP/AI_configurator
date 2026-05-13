"""Integration tests for POST /api/execute/{action_id}.

The endpoint runs an approved tool directly (no LLM round-trip). Tests
mock the tool dispatcher so they don't need a real router or
Anthropic key; the integration with FastAPI's threadpool + Pydantic
+ approval gate is what we're testing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.api.routes_approvals as approvals_mod
from backend.main import app
from backend.orchestration.confirmations import (
    approve_action,
    propose_action,
    reject_action,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_execute_404_for_unknown_action(client: TestClient) -> None:
    r = client.post("/api/execute/act_does_not_exist")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_execute_409_for_proposed_but_not_approved(client: TestClient) -> None:
    # Audit B1 fix: the route now uses an atomic CAS (try_begin_execution)
    # that maps WrongState → 409 Conflict. 409 is the right code: the
    # resource exists but is in the wrong state for this operation — not
    # 403 (which means "you lack permission").
    aid = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    r = client.post(f"/api/execute/{aid}")
    assert r.status_code == 409
    assert "PROPOSED" in r.json()["detail"]


def test_execute_409_for_rejected(client: TestClient) -> None:
    aid = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    reject_action(aid)
    r = client.post(f"/api/execute/{aid}")
    assert r.status_code == 409
    assert "REJECTED" in r.json()["detail"]


def test_execute_dispatches_approved_action(client: TestClient) -> None:
    """The full happy path: propose -> approve -> /api/execute -> dispatch.

    We patch execute_tool at the routes_approvals module level so we
    don't need a real Playwright browser or router.
    """
    aid = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    approve_action(aid)

    fake_result = {
        "tool": "webui_add_access_vlan",
        "vlan_id": 30,
        "vlan_name": "OFFICE",
        "verified": True,
    }
    with patch.object(approvals_mod, "execute_tool", return_value=fake_result) as mocked:
        r = client.post(f"/api/execute/{aid}")

    assert r.status_code == 200
    body = r.json()
    assert body["action_id"] == aid
    assert body["tool"] == "webui_add_access_vlan"
    assert body["result"] == fake_result
    # Verify the dispatcher got called with the right params (action_id injected)
    mocked.assert_called_once_with(
        "webui_add_access_vlan",
        {"vlan_id": 30, "vlan_name": "OFFICE", "action_id": aid},
    )


def test_execute_500_on_tool_exception(client: TestClient) -> None:
    aid = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    approve_action(aid)

    with patch.object(
        approvals_mod, "execute_tool", side_effect=RuntimeError("playwright crashed")
    ):
        r = client.post(f"/api/execute/{aid}")

    assert r.status_code == 500
    assert "playwright crashed" in r.json()["detail"]
