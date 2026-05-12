"""Approval gate endpoints.

POST /api/approve/{action_id}  — mark an action APPROVED
POST /api/reject/{action_id}   — mark an action REJECTED
GET  /api/actions/{action_id}  — read current state

All transitions happen inside `confirmations._lock` so a concurrent
reject between this route's existence-check and the state mutation
cannot wipe an approval. The handlers call the state-transition
function directly and let it raise KeyError → 404 if the action_id
is unknown — no double-fetch.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.orchestration.confirmations import (
    approve_action,
    get_action,
    reject_action,
)

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
