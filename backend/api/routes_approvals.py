"""Approval gate endpoints.

POST /api/approve/{action_id}  — mark an action APPROVED
POST /api/reject/{action_id}   — mark an action REJECTED
GET  /api/actions/{action_id}  — read current state
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.orchestration.confirmations import (
    approve_action,
    get_action,
    reject_action,
)

router = APIRouter(prefix="/api", tags=["approvals"])


def _get_or_404(action_id: str) -> dict:
    try:
        return get_action(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"action_id {action_id!r} not found") from exc


@router.post("/approve/{action_id}")
async def approve(action_id: str) -> dict:
    action = _get_or_404(action_id)
    return approve_action(action["action_id"])


@router.post("/reject/{action_id}")
async def reject(action_id: str) -> dict:
    action = _get_or_404(action_id)
    return reject_action(action["action_id"])


@router.get("/actions/{action_id}")
async def get_action_status(action_id: str) -> dict:
    return _get_or_404(action_id)
