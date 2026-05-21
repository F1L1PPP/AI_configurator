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
from datetime import UTC, datetime, timedelta
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

_TERMINAL_STATES: frozenset[ActionState] = frozenset(
    {
        ActionState.EXECUTED,
        ActionState.VERIFIED,
        ActionState.FAILED,
        ActionState.REJECTED,
    }
)

# Lazy-purge cutoff. 24h is comfortably longer than any demo flow, short
# enough that the dict doesn't grow without bound across a long-running
# session. Each propose_action triggers an O(N) scan under _lock — N stays
# small (<1000) for the school-project lab. SQLite migration (Day 12)
# replaces this with a proper retention policy.
_DEFAULT_TTL_SECONDS = 24 * 3600


def _now() -> str:
    return datetime.now(UTC).isoformat()


def purge_terminal_actions_older_than(ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> int:
    """Remove terminal-state actions older than ttl_seconds. Returns count purged.

    Terminal states: EXECUTED, VERIFIED, FAILED, REJECTED. Non-terminal
    actions (PROPOSED, APPROVED, EXECUTING) are NEVER purged regardless of
    age — purging an in-flight execution would lose the result on the
    routes_approvals callback path.

    Idempotent: calling twice in a row is a no-op.
    """
    cutoff_iso = (datetime.now(UTC) - timedelta(seconds=ttl_seconds)).isoformat()
    with _lock:
        to_remove = [
            aid
            for aid, a in _actions.items()
            if a["state"] in _TERMINAL_STATES and a.get("updated_at", "") < cutoff_iso
        ]
        for aid in to_remove:
            del _actions[aid]
        return len(to_remove)


def propose_action(tool: str, params: dict, preview_meta: dict | None = None) -> str:
    """Register a new action and return its action_id.

    ``preview_meta`` carries propose-time conflict-detection fields
    (existing_entity, existing_block, is_exact_match) that are needed by
    the UI but must NOT appear in ``params``, because ``params`` is splatted
    directly into the executor function via ``func(**params)``.
    """
    # Lazy purge: terminal actions older than the TTL get evicted on every
    # propose. Cheap O(N) scan under the lock; no scheduler needed.
    purge_terminal_actions_older_than()
    now = _now()
    action_id = f"act_{datetime.now(UTC).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    with _lock:
        _actions[action_id] = {
            "action_id": action_id,
            "tool": tool,
            "params": params,
            "preview_meta": copy.deepcopy(preview_meta) if preview_meta is not None else None,
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
    """Mark an action APPROVED — only allowed from PROPOSED.

    Tightened to refuse re-approval after EXECUTED / FAILED / VERIFIED
    / EXECUTING: a lenient approve_action would let a finished action be
    re-armed, and /api/execute would happily run it again (duplicate
    write to the router). The UI's inFlight ref already prevents
    double-click submits on the same action; this is the server-side
    backstop.

    Raises:
        KeyError:   action_id unknown
        WrongState: action is not in PROPOSED state
    """
    return _transition_if(
        action_id,
        expected={ActionState.PROPOSED},
        new_state=ActionState.APPROVED,
    )


def get_state(action_id: str) -> ActionState | None:
    """Return the current state of an action, or None if unknown.

    Cheaper than `get_action` for the common "did the write tool already
    transition the state?" check that the /api/execute route does after
    a structured-error result."""
    with _lock:
        action = _actions.get(action_id)
        return action["state"] if action is not None else None


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


def mark_failed(action_id: str, result: dict | None = None) -> dict:
    """Transition to FAILED. Optionally persist a result dict on the action
    so that debug_sweep can retrieve it later via get_action(action_id)["result"].

    Backward-compatible: existing callers that pass no `result` get the old behaviour.
    """
    transitioned = _transition(action_id, ActionState.FAILED)
    with _lock:
        if action_id in _actions:
            _actions[action_id]["result"] = copy.deepcopy(result) if result is not None else None
    return transitioned


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


def find_most_recent_failure() -> dict | None:
    """Return the (deepcopied) result dict of the most recently FAILED action
    that has a stored result, or None if no such action exists.

    Used by ``_propose_debug_sweep`` as a server-side fallback for the
    auto-debug flow: when the LLM doesn't extract ``failure_action_id``
    from a "Please diagnose action_id=X failed" user message, this
    function recovers the failure context anyway so the diagnostic plan
    stays focused instead of degrading to a broad sweep.

    Returns the result deepcopied so callers can't mutate stored state.
    """
    with _lock:
        candidates = [
            a for a in _actions.values() if a.get("state") == ActionState.FAILED and a.get("result")
        ]
        if not candidates:
            return None
        # Most recently updated wins. `updated_at` is set by every transition
        # (including mark_failed), so the most-recent FAILED action is the
        # one whose updated_at is highest.
        candidates.sort(key=lambda a: a.get("updated_at", ""), reverse=True)
        return copy.deepcopy(candidates[0]["result"])


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
