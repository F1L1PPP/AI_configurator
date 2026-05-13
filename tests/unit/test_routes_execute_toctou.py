"""Regression test for audit-B1 — /api/execute closes the TOCTOU window.

End-to-end through FastAPI's TestClient: propose → approve → execute,
and verify that a concurrent /api/reject between approve and execute is
refused with 409 (because /api/execute pre-transitions APPROVED →
EXECUTING atomically before dispatching the write tool).
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import (
    ActionState,
    approve_action,
    get_action,
    propose_action,
)

# _clean_actions fixture is in tests/conftest.py (autouse).


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def stub_show_version(monkeypatch):
    """Replace show_version with a deterministic stub so the test doesn't
    need a real router. Returns a stub controllable mid-test."""

    def _stub(**_kwargs):
        return {"version": "stub"}

    monkeypatch.setitem(tr._TOOL_FUNCS, "show_version", _stub)
    return _stub


# ---------------------------------------------------------------------------
# Happy path — /api/execute against an approved action works
# ---------------------------------------------------------------------------


def test_execute_after_approve_returns_200(client, monkeypatch):
    """Stub set_hostname so we don't need a router. The route should
    return 200 with the tool result."""
    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    approve_action(action_id)

    monkeypatch.setitem(
        tr._TOOL_FUNCS,
        "set_hostname",
        lambda **kw: {"ok": True, "got": kw},
    )

    resp = client.post(f"/api/execute/{action_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_id"] == action_id
    assert body["tool"] == "set_hostname"
    # Action state has been transitioned to EXECUTING by the route, then
    # the stub returns without calling mark_executed. The endpoint
    # contract is just "dispatched the tool"; mark_executed lives in the
    # real write tool's success path.
    assert get_action(action_id)["state"] == ActionState.EXECUTING


# ---------------------------------------------------------------------------
# 409 paths — wrong state
# ---------------------------------------------------------------------------


def test_execute_returns_409_when_not_approved(client):
    """An action still in PROPOSED state can't be executed — 409 Conflict."""
    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    resp = client.post(f"/api/execute/{action_id}")
    assert resp.status_code == 409
    assert "PROPOSED" in resp.json()["detail"]


def test_execute_returns_409_when_already_rejected(client):
    """Rejected actions cannot be executed."""
    from backend.orchestration.confirmations import reject_action

    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    reject_action(action_id)

    resp = client.post(f"/api/execute/{action_id}")
    assert resp.status_code == 409
    assert "REJECTED" in resp.json()["detail"]


def test_execute_returns_404_when_unknown(client):
    resp = client.post("/api/execute/act_does_not_exist")
    assert resp.status_code == 404


def test_execute_second_call_returns_409(client, monkeypatch):
    """Two /api/execute calls on the same action_id — only the first wins.
    Defends against a UI double-click slipping past the inFlight ref."""
    action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
    approve_action(action_id)

    monkeypatch.setitem(tr._TOOL_FUNCS, "set_hostname", lambda **kw: {"ok": True})

    first = client.post(f"/api/execute/{action_id}")
    second = client.post(f"/api/execute/{action_id}")

    assert first.status_code == 200
    assert second.status_code == 409  # already EXECUTING
    assert "EXECUTING" in second.json()["detail"]


# ---------------------------------------------------------------------------
# The TOCTOU race itself: concurrent execute + reject must not both win
# ---------------------------------------------------------------------------


def test_concurrent_execute_and_reject(client, monkeypatch):
    """Audit B1 regression: two concurrent HTTP requests — POST /execute
    and POST /reject — on the same APPROVED action. Exactly one must
    succeed. The other gets 409 Conflict (because the atomic
    try_begin_execution / tightened reject_action refuse the late call)."""
    successes: list[str] = []
    failures: list[int] = []
    lock = threading.Lock()

    # Slow down the stubbed tool so the race window is wide enough to
    # exercise the lock even on fast machines.
    def slow_tool(**_kw):
        time.sleep(0.05)
        return {"ok": True}

    monkeypatch.setitem(tr._TOOL_FUNCS, "set_hostname", slow_tool)

    rounds = 50
    for _ in range(rounds):
        action_id = propose_action("set_hostname", {"new_name": "LAB-R1"})
        approve_action(action_id)
        barrier = threading.Barrier(2)

        def execute_worker(aid=action_id, b=barrier):
            # Bind barrier as default arg so the closure pins this loop
            # iteration's instance, not the shared loop variable (B023).
            b.wait()
            r = client.post(f"/api/execute/{aid}")
            with lock:
                if r.status_code == 200:
                    successes.append("execute")
                else:
                    failures.append(r.status_code)

        def reject_worker(aid=action_id, b=barrier):
            b.wait()
            r = client.post(f"/api/reject/{aid}")
            with lock:
                if r.status_code == 200:
                    successes.append("reject")
                else:
                    failures.append(r.status_code)

        t1 = threading.Thread(target=execute_worker)
        t2 = threading.Thread(target=reject_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    # Each of the {rounds} races must produce exactly one 2xx and one 409.
    # The exact split (which racer won how many times) is timing-dependent
    # but the invariant — no round had two winners — must hold.
    assert len(successes) == rounds, f"expected {rounds} winners, got {len(successes)}: {successes}"
    assert len(failures) == rounds, f"expected {rounds} losers, got {len(failures)}: {failures}"
    assert all(code == 409 for code in failures), failures
