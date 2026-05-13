"""HITL (Human-In-The-Loop) approval gate.

Every write tool requires an action_id that has been explicitly approved via
POST /api/approve/{action_id} before it will execute. In-memory store for
Day 3; Day 12 migrates this to SQLite.

Thread safety: FastAPI runs handlers in a threadpool (via `run_in_threadpool`
in routes_chat) AND the approval routes hit the same store from the event
loop. Multiple threads can read/check/mutate concurrently, so every
mutation and every consistency-critical read goes through `_lock`. The
lock is a small re-entrant mutex; the SQLite migration replaces it.

States:
    PROPOSED  → created, waiting for human decision
    APPROVED  → human clicked Approve; write tools will execute
    EXECUTING → execution started (atomic transition from APPROVED) —
                blocks a late /api/reject from clobbering an in-flight write
    REJECTED  → human clicked Reject; write tools will refuse
    EXECUTED  → write completed successfully
    VERIFIED  → post-snapshot confirmed the change
    FAILED    → write attempted but errored
"""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import UTC, datetime
from enum import Enum


class ActionState(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class NotApproved(Exception):
    """Raised when a write tool is called without prior approval."""


class WrongState(Exception):
    """Raised when an atomic CAS transition can't fire because the action
    is not in the expected state. Carries the current + expected states so
    the route layer can map them to a meaningful HTTP response."""

    def __init__(self, action_id: str, current: ActionState, expected: set[ActionState]) -> None:
        self.action_id = action_id
        self.current = current
        self.expected = expected
        super().__init__(
            f"action_id {action_id!r} is in state {current.value!r}; "
            f"expected one of {sorted(s.value for s in expected)!r}"
        )


# In-memory store: action_id → action dict. Guarded by _lock.
_actions: dict[str, dict] = {}
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def propose_action(tool: str, params: dict) -> str:
    """Register a new action and return its action_id."""
    now = _now()
    action_id = f"act_{datetime.now(UTC).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    with _lock:
        _actions[action_id] = {
            "action_id": action_id,
            "tool": tool,
            "params": params,
            "state": ActionState.PROPOSED,
            "created_at": now,
            "updated_at": now,
        }
    return action_id


def _transition(action_id: str, new_state: ActionState) -> dict:
    """Unconditional state transition + timestamp under the lock.

    Snapshot-and-return: callers receive a deep copy so the nested params
    dict they get back can't be mutated by a later thread either.
    """
    with _lock:
        if action_id not in _actions:
            raise KeyError(f"Unknown action_id: {action_id!r}")
        action = _actions[action_id]
        action["state"] = new_state
        action["updated_at"] = _now()
        return copy.deepcopy(action)


def _transition_if(
    action_id: str,
    expected: set[ActionState],
    new_state: ActionState,
) -> dict:
    """Atomic compare-and-swap: only transition if current state ∈ expected.

    This is the primitive that closes the TOCTOU race on /api/execute —
    check + mutate happen inside a single lock acquisition, so no
    concurrent /api/reject can slip between them.

    Raises:
        KeyError:    action_id unknown
        WrongState:  current state is not in `expected`
    """
    with _lock:
        if action_id not in _actions:
            raise KeyError(f"Unknown action_id: {action_id!r}")
        action = _actions[action_id]
        current = action["state"]
        if current not in expected:
            raise WrongState(action_id, current, expected)
        action["state"] = new_state
        action["updated_at"] = _now()
        return copy.deepcopy(action)


def approve_action(action_id: str) -> dict:
    """Mark an action APPROVED. Lenient — accepts from any current state
    so a UI double-click doesn't error. The real safety gate is
    `try_begin_execution`, not this function."""
    return _transition(action_id, ActionState.APPROVED)


def reject_action(action_id: str) -> dict:
    """Reject only from PROPOSED or APPROVED — once execution starts
    (EXECUTING) or finishes (EXECUTED/FAILED) a reject would clobber a
    real result. Raises WrongState if the action has moved past APPROVED."""
    return _transition_if(
        action_id,
        expected={ActionState.PROPOSED, ActionState.APPROVED},
        new_state=ActionState.REJECTED,
    )


def try_begin_execution(action_id: str) -> dict:
    """Atomic transition APPROVED → EXECUTING. The /api/execute endpoint
    calls this to lock in the approval before dispatching the write tool,
    closing the window where a concurrent /api/reject could wipe approval
    between the route's existence check and the dispatcher's gate check.

    Raises:
        KeyError:   action_id unknown (→ HTTP 404)
        WrongState: action is not in APPROVED state (→ HTTP 409)
    """
    return _transition_if(
        action_id,
        expected={ActionState.APPROVED},
        new_state=ActionState.EXECUTING,
    )


def mark_executed(action_id: str) -> dict:
    """Transition to EXECUTED. Lenient — write tools call this on success
    regardless of whether they came from /api/execute (state EXECUTING)
    or from the planner loop (state APPROVED). Both paths converge here."""
    return _transition(action_id, ActionState.EXECUTED)


def mark_failed(action_id: str) -> dict:
    return _transition(action_id, ActionState.FAILED)


def is_approved(action_id: str) -> bool:
    """Atomic check that the action exists AND is authorised to execute.

    Accepts both APPROVED (planner loop path: write tool runs directly
    after approval) and EXECUTING (/api/execute path: state was pre-
    transitioned by `try_begin_execution`). Both are "the operator said
    yes and execution hasn't finished" — the right gate for write tools."""
    with _lock:
        action = _actions.get(action_id)
        if action is None:
            return False
        return action["state"] in (ActionState.APPROVED, ActionState.EXECUTING)


def get_action(action_id: str) -> dict:
    """Return a deep copy of the action dict (safe to read outside lock)."""
    with _lock:
        if action_id not in _actions:
            raise KeyError(f"Unknown action_id: {action_id!r}")
        return copy.deepcopy(_actions[action_id])


def _reset_for_testing() -> None:
    """Clear all in-memory state. Called by test fixtures only."""
    with _lock:
        _actions.clear()
