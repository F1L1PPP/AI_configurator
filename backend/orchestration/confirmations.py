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
    REJECTED  → human clicked Reject; write tools will refuse
    EXECUTED  → write completed successfully
    VERIFIED  → post-snapshot confirmed the change
    FAILED    → write attempted but errored
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from enum import Enum


class ActionState(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class NotApproved(Exception):
    """Raised when a write tool is called without prior approval."""


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
    """Look up + state-transition + timestamp under the lock.

    Snapshot-and-return: callers receive a shallow copy so the dict they
    get back can't be mutated by a later thread.
    """
    with _lock:
        if action_id not in _actions:
            raise KeyError(f"Unknown action_id: {action_id!r}")
        action = _actions[action_id]
        action["state"] = new_state
        action["updated_at"] = _now()
        return dict(action)


def approve_action(action_id: str) -> dict:
    return _transition(action_id, ActionState.APPROVED)


def reject_action(action_id: str) -> dict:
    return _transition(action_id, ActionState.REJECTED)


def mark_executed(action_id: str) -> dict:
    return _transition(action_id, ActionState.EXECUTED)


def mark_failed(action_id: str) -> dict:
    return _transition(action_id, ActionState.FAILED)


def is_approved(action_id: str) -> bool:
    """Atomic check that the action exists AND is in APPROVED state."""
    with _lock:
        action = _actions.get(action_id)
        return action is not None and action["state"] == ActionState.APPROVED


def get_action(action_id: str) -> dict:
    """Return a shallow copy of the action dict (safe to read outside lock)."""
    with _lock:
        if action_id not in _actions:
            raise KeyError(f"Unknown action_id: {action_id!r}")
        return dict(_actions[action_id])


def _reset_for_testing() -> None:
    """Clear all in-memory state. Called by test fixtures only."""
    with _lock:
        _actions.clear()
