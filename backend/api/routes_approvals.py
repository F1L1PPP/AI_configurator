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
    get_state,
    mark_failed,
    reject_action,
    try_begin_execution,
)
from backend.orchestration.tool_registry import execute_tool

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["approvals"])

# Map error keys returned by execute_tool's structured-error path to HTTP
# status codes. The dispatcher converts internal exceptions into these
# error keys instead of raising, so the route has to translate them back
# into client-meaningful statuses.
_ERROR_TO_STATUS: dict[str, int] = {
    # action_id missing or revoked between try_begin_execution and the
    # dispatcher gate — shouldn't happen in practice now (we hold the
    # state in EXECUTING) but the dispatcher's layer-2 check still fires
    # for any unforeseen race. 409 because the resource is in the wrong
    # state, not 403 (permission).
    "not_approved": 409,
    # Tool-name typo or schema drift — the propose path enforces the
    # schema, so reaching this from /api/execute means a stale/tampered
    # action_id. 404 communicates "the named tool isn't in the registry".
    "unknown tool": 404,
    # Validators rejected the params — same category as a malformed
    # request body. 422 Unprocessable Entity matches FastAPI's convention.
    "bad_parameters": 422,
    # Tool raised an unhandled exception. 500.
    "tool_failed": 500,
    # Inner LLM (Haiku) was overloaded during a propose step. 503 matches
    # the outer-planner case so callers see a consistent status code.
    "llm_overloaded": 503,
}


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
    except WrongState as exc:
        # approve_action is now PROPOSED-only. A late approve (after
        # reject/execute/etc.) returns 409 so the UI can show "this
        # action can't be approved in its current state" instead of a
        # silent success that re-enables /api/execute on a finished
        # action.
        log.info(
            "approve_wrong_state",
            action_id=action_id,
            current=exc.current.value,
        )
        raise _wrong_state_to_409(exc) from exc


@router.post("/reject/{action_id}")
async def reject(action_id: str) -> dict:
    try:
        return reject_action(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc
    except WrongState as exc:
        # Tried to reject something that's already executing/executed/failed.
        log.info("reject_wrong_state", action_id=action_id, current=exc.current.value)
        raise _wrong_state_to_409(exc) from exc


@router.get("/actions/{action_id}")
async def get_action_status(action_id: str) -> dict:
    try:
        return get_action(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc


def _result_is_error(result: object) -> str | None:
    """If the tool dispatcher returned a structured error dict, return
    the error key; otherwise None. The dispatcher's convention is
    `{"error": "<key>", "message": "..."}`."""
    if isinstance(result, dict) and "error" in result:
        err = result["error"]
        return err if isinstance(err, str) else "tool_failed"
    return None


@router.post("/execute/{action_id}")
async def execute(action_id: str) -> dict:
    """Run the approved tool directly — no LLM round-trip.

    Lets the operator complete an action from /preview alone (Approve →
    Execute Now) without going back to /chat and saying "execute it".

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
      4. Translate the outcome:
         - tool returned a normal result → 200, state should be EXECUTED
           (write tools call mark_executed themselves on success).
         - tool returned a structured error dict → translate to a
           4xx/5xx by `_ERROR_TO_STATUS` and mark FAILED, because
           execute_tool's error path doesn't always fire the write
           tool's own except-block. Without this, the action would be
           stuck in EXECUTING forever (and reject_action would refuse
           to clear it).
         - tool raised → 500 + mark FAILED.
    """
    try:
        action = try_begin_execution(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc
    except WrongState as exc:
        log.info(
            "execute_wrong_state",
            action_id=action_id,
            current=exc.current.value,
        )
        raise _wrong_state_to_409(exc) from exc

    tool = action["tool"]
    params = {**action["params"], "action_id": action_id}
    log.info("execute_dispatch", action_id=action_id, tool=tool)

    try:
        result = await run_in_threadpool(execute_tool, tool, params)
    except Exception as exc:
        # execute_tool catches most exceptions itself and returns an
        # error dict (handled below). Anything that escapes here is a
        # real programming bug — log loudly, mark FAILED, return 500.
        with contextlib.suppress(KeyError):
            mark_failed(action_id)
        log.error(
            "execute_unhandled_exception",
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

    # Structured-error path: dispatcher returned {"error": "<key>", ...}.
    # The write tool's own except-block already calls mark_failed for
    # the cases it owns, but for failures upstream of the tool (unknown
    # tool, dispatcher's layer-1 not_approved, propose-time validators)
    # nothing transitions the state. Do it here so the store can't be
    # stuck in EXECUTING with no way out.
    err_key = _result_is_error(result)
    if err_key is not None:
        # Only flip to FAILED if we're still in EXECUTING. The write
        # tool's mark_failed may have already moved us — be idempotent.
        if get_state(action_id) is not None and get_state(action_id).value == "EXECUTING":
            with contextlib.suppress(KeyError):
                mark_failed(action_id)
        status = _ERROR_TO_STATUS.get(err_key, 500)
        message = result.get("message") if isinstance(result, dict) else None
        log.warning(
            "execute_tool_error",
            action_id=action_id,
            tool=tool,
            error_key=err_key,
            status=status,
        )
        raise HTTPException(
            status_code=status,
            detail=f"execute_tool({tool}) -> {err_key}: {message or 'no message'}",
        )

    return {"action_id": action_id, "tool": tool, "result": result}
