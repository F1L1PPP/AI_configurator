"""Approval gate endpoints.

POST /api/approve/{action_id}  — mark an action APPROVED
POST /api/reject/{action_id}   — mark an action REJECTED
POST /api/execute/{action_id}  — run the approved tool directly (no LLM)
GET  /api/actions/{action_id}  — read current state

All transitions happen inside `confirmations._lock`. The /api/execute
endpoint uses `try_begin_execution` — an atomic compare-and-swap that
transitions APPROVED → EXECUTING under the same lock acquisition that
checks the state. This closes the TOCTOU window where a concurrent
/api/reject could slip between an existence-check and the dispatch.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from backend.core.logging import get_logger
from backend.orchestration.confirmations import (
    WrongState,
    approve_action,
    get_action,
    mark_failed,
    reject_action,
    try_begin_execution,
)
from backend.orchestration.tool_registry import execute_tool

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["approvals"])


def _key_error_to_404(action_id: str, exc: KeyError) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"action_id {action_id!r} not found",
    )


def _wrong_state_to_409(exc: WrongState) -> HTTPException:
    """Map an atomic-transition failure to HTTP 409 Conflict.

    409 (not 403) because the failure is "the resource is in the wrong
    state for this operation", not "you lack permission". The operator
    may retry after the action goes back to a valid state — though for
    most flows the right answer is "propose a new action_id".
    """
    return HTTPException(
        status_code=409,
        detail=str(exc),
    )


@router.post("/approve/{action_id}")
async def approve(action_id: str) -> dict:
    try:
        return approve_action(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc


@router.post("/reject/{action_id}")
async def reject(action_id: str) -> dict:
    try:
        return reject_action(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc
    except WrongState as exc:
        # Tried to reject something that's already executing/executed/failed.
        log.info("reject_wrong_state", action_id=action_id, current=str(exc.current))
        raise _wrong_state_to_409(exc) from exc


@router.get("/actions/{action_id}")
async def get_action_status(action_id: str) -> dict:
    try:
        return get_action(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc


@router.post("/execute/{action_id}")
async def execute(action_id: str) -> dict:
    """Run the approved tool directly — no LLM round-trip.

    Lets the operator complete an action from /preview alone (Approve →
    Execute Now) without going back to /chat and saying "execute it".
    Fixes the UX trap where the LLM lost the original tool_use context
    after page navigation and couldn't resolve action_id references.

    Flow:
      1. Atomically transition APPROVED → EXECUTING (`try_begin_execution`).
         404 if action unknown, 409 if state is anything but APPROVED.
         Once in EXECUTING, a concurrent /api/reject is refused — no
         TOCTOU window where the dispatch sees stale state.
      2. Build params = {**action.params, "action_id": action_id} and
         dispatch via execute_tool() — same code path the LLM would
         take. The dispatcher's defense-in-depth gate accepts EXECUTING
         the same way it accepts APPROVED, so the write tool runs.
      3. Block until the flow returns (run_in_threadpool, since WebUI
         flows can take 20-30s while Playwright drives the browser).
      4. Return the structured tool result. On exception, the action is
         marked FAILED so the in-memory store reflects reality.
    """
    try:
        action = try_begin_execution(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc
    except WrongState as exc:
        log.info(
            "execute_wrong_state",
            action_id=action_id,
            current=str(exc.current),
        )
        raise _wrong_state_to_409(exc) from exc

    tool = action["tool"]
    params = {**action["params"], "action_id": action_id}
    log.info("execute_dispatch", action_id=action_id, tool=tool)

    try:
        result = await run_in_threadpool(execute_tool, tool, params)
    except Exception as exc:
        # The write tool's mark_failed runs inside the tool's own except
        # block, but if execute_tool itself raised before reaching the
        # tool (or the tool's mark_failed didn't fire) we still need the
        # store to leave EXECUTING. Idempotent — mark_failed is lenient.
        with contextlib.suppress(KeyError):
            mark_failed(action_id)
        log.error(
            "execute_failed",
            action_id=action_id,
            tool=tool,
            error=str(exc),
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"execute_tool({tool}) raised: {type(exc).__name__}: {exc}",
        ) from exc

    return {"action_id": action_id, "tool": tool, "result": result}
