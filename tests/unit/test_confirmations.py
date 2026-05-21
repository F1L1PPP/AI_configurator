"""Tests for confirmations.py — action store TTL purge (review fix #2).

White-box note: several tests access `_actions` directly to set up state
that the public API doesn't expose (e.g. backdating `updated_at`). This is
intentional — production code uses the public API only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.orchestration.confirmations import (
    ActionState,
    _actions,  # white-box: direct dict access for state setup
    _reset_for_testing,
    approve_action,
    get_action,
    propose_action,
    purge_terminal_actions_older_than,
    try_begin_execution,
    try_mark_failed_if_executing,
)


@pytest.fixture(autouse=True)
def clean_store():
    """Reset the in-memory store before every test to prevent cross-test pollution."""
    _reset_for_testing()
    yield
    _reset_for_testing()


def _backdate(action_id: str, hours: int) -> None:
    """White-box helper: set updated_at to `hours` ago on an existing action."""
    past = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    _actions[action_id]["updated_at"] = past


# ---------------------------------------------------------------------------
# purge_terminal_actions_older_than
# ---------------------------------------------------------------------------


def test_purge_removes_old_terminal_actions():
    aid = propose_action("tool_a", {"k": "v"})
    # White-box: force into FAILED state and backdate to 48h ago
    _actions[aid]["state"] = ActionState.FAILED
    _backdate(aid, 48)

    removed = purge_terminal_actions_older_than()

    assert removed == 1
    assert aid not in _actions


def test_purge_keeps_recent_terminal_actions():
    aid = propose_action("tool_b", {"k": "v"})
    # White-box: EXECUTED but updated_at is now (within TTL)
    _actions[aid]["state"] = ActionState.EXECUTED
    # updated_at is already "now" from propose_action — no backdating needed

    removed = purge_terminal_actions_older_than()

    assert removed == 0
    assert aid in _actions


def test_purge_keeps_in_flight_actions_regardless_of_age():
    aid = propose_action("tool_c", {"k": "v"})
    # White-box: leave state as PROPOSED (in-flight), but backdate to 48h ago
    assert _actions[aid]["state"] == ActionState.PROPOSED
    _backdate(aid, 48)

    removed = purge_terminal_actions_older_than()

    assert removed == 0
    assert aid in _actions


def test_propose_action_triggers_lazy_purge():
    # Pre-populate store with an old FAILED action
    old_aid = propose_action("old_tool", {"x": 1})
    _actions[old_aid]["state"] = ActionState.FAILED
    _backdate(old_aid, 48)

    # propose_action for a NEW action must evict the old one
    new_aid = propose_action("new_tool", {"y": 2})

    assert old_aid not in _actions, "Old terminal action should have been purged"
    assert new_aid in _actions, "New action must be present"


# ---------------------------------------------------------------------------
# try_mark_failed_if_executing — atomic CAS (review fix #5)
# ---------------------------------------------------------------------------


def test_try_mark_failed_if_executing_succeeds_when_executing():
    """propose → approve → try_begin_execution → atomic CAS transitions to FAILED."""
    aid = propose_action("tool_x", {"k": "v"})
    approve_action(aid)
    try_begin_execution(aid)

    action = try_mark_failed_if_executing(aid, {"error": "boom"})

    assert action is not None
    assert action["state"] == ActionState.FAILED
    # Result attached on the stored action
    stored = get_action(aid)
    assert stored["result"] == {"error": "boom"}


def test_try_mark_failed_if_executing_returns_none_when_not_executing():
    """PROPOSED state → CAS must not fire; returns None; state unchanged."""
    aid = propose_action("tool_y", {"k": "v"})

    result = try_mark_failed_if_executing(aid, {"error": "irrelevant"})

    assert result is None
    assert get_action(aid)["state"] == ActionState.PROPOSED


def test_try_mark_failed_if_executing_returns_none_on_unknown_action():
    """Bogus action_id → returns None instead of raising KeyError."""
    result = try_mark_failed_if_executing("act_bogus_does_not_exist")
    assert result is None


def test_try_mark_failed_if_executing_persists_result_atomically():
    """Successful CAS with a result dict → get_action["result"] returns a
    deep copy (not the same object reference)."""
    aid = propose_action("tool_z", {"k": "v"})
    approve_action(aid)
    try_begin_execution(aid)

    original = {"nested": {"key": "val"}}
    try_mark_failed_if_executing(aid, original)

    stored_result = get_action(aid)["result"]
    assert stored_result == original
    # Must be a deep copy — mutating original must not affect stored result
    original["nested"]["key"] = "mutated"
    assert get_action(aid)["result"]["nested"]["key"] == "val"
