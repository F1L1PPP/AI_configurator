"""Regression tests for the atomic state-machine in confirmations.

Closes the audit-B1 TOCTOU window: between an existence-check on
`get_action()["state"] == APPROVED` and the dispatch in /api/execute,
a concurrent /api/reject used to be able to wipe the approval. The
fix introduces:
  * EXECUTING state
  * try_begin_execution() — atomic CAS APPROVED → EXECUTING
  * reject_action()       — refuses to clobber EXECUTING/EXECUTED/FAILED

These tests guard the contract so future refactors don't reopen the race.
"""

from __future__ import annotations

import threading

import pytest

from backend.orchestration.confirmations import (
    ActionState,
    WrongState,
    approve_action,
    get_action,
    mark_executed,
    mark_failed,
    propose_action,
    reject_action,
    try_begin_execution,
)

# _clean_actions fixture is in tests/conftest.py (autouse).


# ---------------------------------------------------------------------------
# try_begin_execution — the atomic CAS
# ---------------------------------------------------------------------------


def test_try_begin_execution_transitions_approved_to_executing():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)

    action = try_begin_execution(action_id)
    assert action["state"] == ActionState.EXECUTING

    # External read sees the new state too.
    assert get_action(action_id)["state"] == ActionState.EXECUTING


def test_try_begin_execution_refuses_from_proposed():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    with pytest.raises(WrongState) as exc:
        try_begin_execution(action_id)
    assert exc.value.current == ActionState.PROPOSED
    assert ActionState.APPROVED in exc.value.expected


def test_try_begin_execution_refuses_from_rejected():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    reject_action(action_id)
    with pytest.raises(WrongState):
        try_begin_execution(action_id)


def test_try_begin_execution_refuses_double_call():
    """Once in EXECUTING, a second begin_execution must fail — otherwise
    a buggy double-dispatch would race two write-tool invocations."""
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    try_begin_execution(action_id)

    with pytest.raises(WrongState):
        try_begin_execution(action_id)


def test_try_begin_execution_unknown_id_raises_key_error():
    with pytest.raises(KeyError):
        try_begin_execution("act_nonexistent")


# ---------------------------------------------------------------------------
# reject_action — tightened to refuse mid/post-execution
# ---------------------------------------------------------------------------


def test_reject_allowed_from_proposed():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    reject_action(action_id)
    assert get_action(action_id)["state"] == ActionState.REJECTED


def test_reject_allowed_from_approved():
    """Operator changes mind: approve then reject is still valid."""
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    reject_action(action_id)
    assert get_action(action_id)["state"] == ActionState.REJECTED


def test_reject_refuses_executing():
    """The TOCTOU fix: once execution started, reject cannot wipe it."""
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    try_begin_execution(action_id)
    with pytest.raises(WrongState):
        reject_action(action_id)


def test_reject_refuses_executed():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    mark_executed(action_id)
    with pytest.raises(WrongState):
        reject_action(action_id)


def test_reject_refuses_failed():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    mark_failed(action_id)
    with pytest.raises(WrongState):
        reject_action(action_id)


# ---------------------------------------------------------------------------
# approve_action — Copilot follow-up: tightened to PROPOSED-only so a
# post-execution re-approve can't resurrect a finished action and re-arm
# /api/execute (duplicate write).
# ---------------------------------------------------------------------------


def test_approve_refuses_from_executing():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    try_begin_execution(action_id)
    with pytest.raises(WrongState):
        approve_action(action_id)


def test_approve_refuses_from_executed():
    """The critical case — without this guard, an EXECUTED action could
    be re-approved and re-executed (duplicate write to the router)."""
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    mark_executed(action_id)
    with pytest.raises(WrongState):
        approve_action(action_id)


def test_approve_refuses_from_failed():
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    mark_failed(action_id)
    with pytest.raises(WrongState):
        approve_action(action_id)


def test_approve_refuses_from_rejected():
    """Once rejected, the operator should propose a fresh action_id
    rather than un-rejecting. Clean state machine."""
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    reject_action(action_id)
    with pytest.raises(WrongState):
        approve_action(action_id)


def test_approve_idempotency_double_click_now_409s():
    """UI inFlight ref already prevents double-clicks but the server-side
    rule means a second approve will 409 instead of being a silent no-op."""
    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    with pytest.raises(WrongState):
        approve_action(action_id)


# ---------------------------------------------------------------------------
# is_approved — accepts both APPROVED and EXECUTING so write tools' _guard
# stays green while a /api/execute-driven flow is in progress.
# ---------------------------------------------------------------------------


def test_is_approved_true_when_executing():
    from backend.orchestration.confirmations import is_approved

    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    try_begin_execution(action_id)
    assert is_approved(action_id) is True


def test_is_approved_false_when_executed():
    """Once a write completes (mark_executed), is_approved returns False —
    re-running an EXECUTED action would be a duplicate write."""
    from backend.orchestration.confirmations import is_approved

    action_id = propose_action("set_hostname", {"new_name": "R1"})
    approve_action(action_id)
    mark_executed(action_id)
    assert is_approved(action_id) is False


# ---------------------------------------------------------------------------
# get_action returns a deep copy — caller mutation can't poison the store
# ---------------------------------------------------------------------------


def test_get_action_returns_deep_copy():
    """Caller mutating params on the returned dict must not affect the
    store — the previous shallow copy let nested-dict mutation leak back."""
    action_id = propose_action("set_hostname", {"new_name": "R1"})

    snapshot = get_action(action_id)
    snapshot["params"]["new_name"] = "HACKED"

    fresh = get_action(action_id)
    assert fresh["params"]["new_name"] == "R1"


# ---------------------------------------------------------------------------
# Concurrency — try_begin_execution vs reject MUST yield one winner,
# never both succeeding.
# ---------------------------------------------------------------------------


def test_concurrent_begin_execution_vs_reject_yields_exactly_one_winner():
    """Two threads race: one tries to begin execution, one tries to reject.
    Exactly one must succeed; the loser sees WrongState. The action ends
    up in either EXECUTING (begin won) or REJECTED (reject won) — never
    a torn state."""
    successes: list[str] = []
    failures: list[type] = []
    results_lock = threading.Lock()

    for _ in range(100):  # repeat to exercise the race
        action_id = propose_action("set_hostname", {"new_name": "R1"})
        approve_action(action_id)
        barrier = threading.Barrier(2)

        def begin_worker(aid=action_id, b=barrier) -> None:
            # Bind barrier as a default arg so the closure pins this
            # iteration's instance, not the loop variable (B023).
            b.wait()
            try:
                try_begin_execution(aid)
                with results_lock:
                    successes.append("begin")
            except WrongState:
                with results_lock:
                    failures.append(WrongState)

        def reject_worker(aid=action_id, b=barrier) -> None:
            b.wait()
            try:
                reject_action(aid)
                with results_lock:
                    successes.append("reject")
            except WrongState:
                with results_lock:
                    failures.append(WrongState)

        t1 = threading.Thread(target=begin_worker)
        t2 = threading.Thread(target=reject_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        final = get_action(action_id)["state"]
        # Final state is the winner's transition target.
        assert final in (ActionState.EXECUTING, ActionState.REJECTED)

    # Across all 100 races: each round had exactly one success + one
    # WrongState failure. (No round where both succeeded — that would be
    # the TOCTOU bug returning.)
    assert len(successes) == 100
    assert len(failures) == 100
