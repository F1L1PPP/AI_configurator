"""HITL (Human-In-The-Loop) approval gate.

Every write tool requires an action_id that has been explicitly approved via
POST /api/approve/{action_id} before it will execute. In-memory store for
Day 3; Day 12 migrates this to SQLite.

States:
    PROPOSED  → created, waiting for human decision
    APPROVED  → human clicked Approve; write tools will execute
    REJECTED  → human clicked Reject; write tools will refuse
    EXECUTED  → write completed successfully
    VERIFIED  → post-snapshot confirmed the change
    FAILED    → write attempted but errored
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum


class ActionState(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED   = "FAILED"


class NotApproved(Exception):
    """Raised when a write tool is called without prior approval."""


# In-memory store: action_id → action dict
_actions: dict[str, dict] = {}


def propose_action(tool: str, params: dict) -> str:
    """Register a new action and return its action_id."""
    now = datetime.now(UTC).isoformat()
    action_id = f"act_{datetime.now(UTC).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    _actions[action_id] = {
        "action_id":  action_id,
        "tool":       tool,
        "params":     params,
        "state":      ActionState.PROPOSED,
        "created_at": now,
        "updated_at": now,
    }
    return action_id


def approve_action(action_id: str) -> dict:
    action = _require(action_id)
    action["state"]      = ActionState.APPROVED
    action["updated_at"] = datetime.now(UTC).isoformat()
    return action


def reject_action(action_id: str) -> dict:
    action = _require(action_id)
    action["state"]      = ActionState.REJECTED
    action["updated_at"] = datetime.now(UTC).isoformat()
    return action


def mark_executed(action_id: str) -> dict:
    action = _require(action_id)
    action["state"]      = ActionState.EXECUTED
    action["updated_at"] = datetime.now(UTC).isoformat()
    return action


def mark_failed(action_id: str) -> dict:
    action = _require(action_id)
    action["state"]      = ActionState.FAILED
    action["updated_at"] = datetime.now(UTC).isoformat()
    return action


def is_approved(action_id: str) -> bool:
    action = _actions.get(action_id)
    return action is not None and action["state"] == ActionState.APPROVED


def get_action(action_id: str) -> dict:
    return _require(action_id)


def _require(action_id: str) -> dict:
    if action_id not in _actions:
        raise KeyError(f"Unknown action_id: {action_id!r}")
    return _actions[action_id]


def _reset_for_testing() -> None:
    """Clear all in-memory state. Called by test fixtures only."""
    _actions.clear()
