"""Unit tests for orchestration.confirmations — in-memory HITL gate."""

from __future__ import annotations

import pytest

from backend.orchestration.confirmations import (
    ActionState,
    _reset_for_testing,
    approve_action,
    get_action,
    is_approved,
    mark_executed,
    mark_failed,
    propose_action,
    reject_action,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_testing()
    yield
    _reset_for_testing()


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
