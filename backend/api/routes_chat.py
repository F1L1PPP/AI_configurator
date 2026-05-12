"""POST /api/chat — talk to the orchestrator.

Synchronous request/response for Day 4. Streaming/WebSocket is Day 11.

Request shape:
    { "message": "show me the interfaces",
      "history": [...optional prior messages...] }

Response shape:
    { "final_text": "Vlan1 is up at 192.168.10.1 ...",
      "events": [{"kind": "tool_call", ...}, ...],
      "history": [...full conversation, pass back on next turn...],
      "stop_reason": "end_turn",
      "awaiting_approval": "act_..." | null }
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.logging import get_logger
from backend.orchestration.planner import PlannerEvent, run_planner

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    final_text:        str
    events:            list[dict[str, Any]]
    history:           list[dict[str, Any]]
    stop_reason:       str
    awaiting_approval: str | None = None


def _event_to_dict(ev: PlannerEvent) -> dict[str, Any]:
    return {"kind": ev.kind, "data": ev.data}


def _pending_approval(events: list[PlannerEvent]) -> str | None:
    """Return the last action_id awaiting approval, if any."""
    for ev in reversed(events):
        if ev.kind == "awaiting_approval":
            return ev.data.get("action_id")
    return None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    try:
        result = run_planner(req.message, history=req.history)
    except Exception as exc:
        log.error("planner_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"planner failed: {exc}") from exc

    return ChatResponse(
        final_text=result.final_text,
        events=[_event_to_dict(ev) for ev in result.events],
        history=result.messages,
        stop_reason=result.stop_reason,
        awaiting_approval=_pending_approval(result.events),
    )
