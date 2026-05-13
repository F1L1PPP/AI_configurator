"""Unit tests for orchestration.confirmations — in-memory HITL gate."""

from __future__ import annotations

import pytest

from backend.orchestration.confirmations import (
    ActionState,
    approve_action,
    get_action,
    is_approved,
    mark_executed,
    mark_failed,
    propose_action,
    reject_action,
)

# _clean_actions fixture is now in tests/conftest.py (autouse).


# ---------------------------------------------------------------------------
# propose_action
# ---------------------------------------------------------------------------


def test_propose_returns_action_id():
    action_id = propose_action("set_hostname", {"name": "LAB-R1"})
    assert action_id.startswith("act_")


def test_proposed_state_is_proposed():
    action_id = propose_action("set_hostname", {"name": "LAB-R1"})
    action = get_action(action_id)
    assert action["state"] == ActionState.PROPOSED


def test_is_approved_false_before_approval():
    action_id = propose_action("set_hostname", {"name": "LAB-R1"})
    assert is_approved(action_id) is False


def test_unknown_action_id_raises_key_error():
    with pytest.raises(KeyError):
        get_action("act_nonexistent")


# ---------------------------------------------------------------------------
# approve_action
# ---------------------------------------------------------------------------


def test_approve_changes_state_to_approved():
    action_id = propose_action("set_hostname", {"name": "R1"})
    approve_action(action_id)
    assert get_action(action_id)["state"] == ActionState.APPROVED


def test_is_approved_true_after_approval():
    action_id = propose_action("set_hostname", {"name": "R1"})
    approve_action(action_id)
    assert is_approved(action_id) is True


# ---------------------------------------------------------------------------
# reject_action
# ---------------------------------------------------------------------------


def test_reject_changes_state_to_rejected():
    action_id = propose_action("set_hostname", {"name": "R1"})
    reject_action(action_id)
    assert get_action(action_id)["state"] == ActionState.REJECTED


def test_is_approved_false_after_reject():
    action_id = propose_action("set_hostname", {"name": "R1"})
    reject_action(action_id)
    assert is_approved(action_id) is False


# ---------------------------------------------------------------------------
# mark_executed / mark_failed
# ---------------------------------------------------------------------------


def test_mark_executed():
    action_id = propose_action("set_hostname", {"name": "R1"})
    approve_action(action_id)
    mark_executed(action_id)
    assert get_action(action_id)["state"] == ActionState.EXECUTED


def test_mark_failed():
    action_id = propose_action("set_hostname", {"name": "R1"})
    mark_failed(action_id)
    assert get_action(action_id)["state"] == ActionState.FAILED


# ---------------------------------------------------------------------------
# multiple actions are independent
# ---------------------------------------------------------------------------


def test_multiple_actions_independent():
    a1 = propose_action("set_hostname", {"name": "R1"})
    a2 = propose_action("set_hostname", {"name": "R2"})
    approve_action(a1)
    assert is_approved(a1) is True
    assert is_approved(a2) is False


# ---------------------------------------------------------------------------
# Concurrency — the lock has to serialise reads + writes (audit #1)
# ---------------------------------------------------------------------------


def test_concurrent_approve_reject_lands_in_one_consistent_state():
    """Two threads racing approve+reject on the same action_id should leave
    the action in EXACTLY ONE of APPROVED or REJECTED — no torn write."""
    import threading

    from backend.orchestration.confirmations import get_action

    action_id = propose_action("set_hostname", {"name": "R1"})
    barrier = threading.Barrier(2)
    results: list[str] = []

    def approve_worker():
        barrier.wait()
        approve_action(action_id)
        results.append("approve")

    def reject_worker():
        barrier.wait()
        from backend.orchestration.confirmations import reject_action

        reject_action(action_id)
        results.append("reject")

    t1 = threading.Thread(target=approve_worker)
    t2 = threading.Thread(target=reject_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    final_state = get_action(action_id)["state"]
    # Whichever thread won the race, the state is one of the two — not a
    # torn write that left mixed fields.
    assert final_state in (ActionState.APPROVED, ActionState.REJECTED)


def test_concurrent_propose_does_not_collide():
    """100 threads proposing simultaneously must produce 100 distinct
    action_ids and no lost-update on the store."""
    import threading

    from backend.orchestration.confirmations import _actions

    ids: list[str] = []
    ids_lock = threading.Lock()

    def worker():
        aid = propose_action("set_hostname", {"name": "x"})
        with ids_lock:
            ids.append(aid)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 100
    assert len(set(ids)) == 100  # all unique
    assert len(_actions) == 100  # store has every one
