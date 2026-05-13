"""Approval gate endpoints.

POST /api/approve/{action_id}  — mark an action APPROVED
POST /api/reject/{action_id}   — mark an action REJECTED
POST /api/execute/{action_id}  — run the approved tool directly (no LLM)
GET  /api/actions/{action_id}  — read current state

All transitions happen inside `confirmations._lock` so a concurrent
reject between this route's existence-check and the state mutation
cannot wipe an approval. The handlers call the state-transition
function directly and let it raise KeyError → 404 if the action_id
is unknown — no double-fetch.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from backend.core.logging import get_logger
from backend.orchestration.confirmations import (
    ActionState,
    approve_action,
    get_action,
    reject_action,
)
from backend.orchestration.tool_registry import execute_tool

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["approvals"])


def _key_error_to_404(action_id: str, exc: KeyError) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"action_id {action_id!r} not found",
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
      1. Look up the action (404 if unknown).
      2. Verify state == APPROVED (403 if not — can't execute a rejected
         or already-executed action).
      3. Build params = {**action.params, "action_id": action_id} and
         dispatch via execute_tool() — same code path the LLM would
         take. The dispatcher's defense-in-depth gate re-checks the
         APPROVED state before invoking the underlying flow.
      4. Block until the flow returns (run_in_threadpool, since WebUI
         flows can take 20-30s while Playwright drives the browser).
      5. Return the structured tool result.
    """
    try:
        action = get_action(action_id)
    except KeyError as exc:
        raise _key_error_to_404(action_id, exc) from exc

    if action["state"] != ActionState.APPROVED:
        log.info(
            "execute_not_approved",
            action_id=action_id,
            state=action["state"],
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"action_id {action_id!r} is in state {action['state']!r}; "
                "only APPROVED actions can be executed."
            ),
        )

    tool = action["tool"]
    params = {**action["params"], "action_id": action_id}
    log.info("execute_dispatch", action_id=action_id, tool=tool)

    try:
        result = await run_in_threadpool(execute_tool, tool, params)
    except Exception as exc:
        log.error(
            "execute_failed",
            action_id=action_id,
            tool=tool,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail=f"execute_tool({tool}) raised: {type(exc).__name__}: {exc}",
        ) from exc

    return {"action_id": action_id, "tool": tool, "result": result}
