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

from anthropic._exceptions import APIStatusError, OverloadedError
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from netmiko.exceptions import (
    NetMikoAuthenticationException,
    NetMikoTimeoutException,
)
from pydantic import BaseModel, Field

from backend.core.logging import get_logger
from backend.orchestration.planner import PlannerEvent, run_planner

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    final_text: str
    events: list[dict[str, Any]]
    history: list[dict[str, Any]]
    stop_reason: str
    awaiting_approval: str | None = None


def _event_to_dict(ev: PlannerEvent) -> dict[str, Any]:
    # Frontend reads `ev.type` (matches the /ws/agent convention used by
    # adapterEventToStreamLine + synthesizeProposal). The PlannerEvent
    # dataclass uses `kind` internally — rename at the wire boundary so
    # both transports look identical to the React consumer.
    return {"type": ev.kind, "data": ev.data}


def _pending_approval(events: list[PlannerEvent]) -> str | None:
    """Return the last action_id awaiting approval, if any."""
    for ev in reversed(events):
        if ev.kind == "awaiting_approval":
            return ev.data.get("action_id")
    return None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # run_planner does blocking I/O (Anthropic API + Netmiko SSH).
    # Offload to a threadpool so the FastAPI event loop stays free for
    # concurrent requests (BackendStatus + RecentActions poll every few s).
    # Note: NotApproved is intentionally NOT caught here. The tool dispatcher
    # in backend.orchestration.tool_registry.execute_tool() catches it and
    # converts it to {"error": "not_approved"} in the tool result, so the
    # planner returns normally and the front-end sees the not-approved state
    # in the events trace. Catching it here would be dead code.
    try:
        result = await run_in_threadpool(run_planner, req.message, history=req.history)
    except NetMikoAuthenticationException as exc:
        # Wrong SSH credentials — 401 Unauthorized so the operator can fix .env
        log.error("chat_ssh_auth_failed", error=str(exc))
        raise HTTPException(
            status_code=401,
            detail=f"SSH authentication to router failed: {exc}",
        ) from exc
    except (NetMikoTimeoutException, TimeoutError) as exc:
        # Router unreachable or SSH read timed out — 503 Service Unavailable
        log.error("chat_router_unreachable", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail=f"Router unreachable / SSH timeout: {exc}",
        ) from exc
    except ValueError as exc:
        # Input validation failure from write_tools (#2/#3) — 422 Unprocessable.
        # Log with exc_info so the Netmiko/SSH frame is preserved on disk —
        # the HTTP body shows only the validator message (user-friendly),
        # but the stack trace is still available in logs/actions.log for
        # debugging when the chain was: SSH error → wrapped as ValueError.
        log.warning("chat_validation_error", error=str(exc), exc_info=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OverloadedError as exc:
        # HTTP 529 — Anthropic API overloaded. SDK already retried 5 times
        # with exponential backoff before raising. Surface a user-friendly 503.
        request_id = getattr(exc, "request_id", None)
        log.warning("chat_anthropic_overloaded", request_id=request_id)
        raise HTTPException(
            status_code=503,
            detail=(
                "Claude API is temporarily overloaded (HTTP 529). "
                "Already retried 5 times via the SDK. "
                f"Please wait a minute and try again. request_id: {request_id}"
            ),
        ) from exc
    except APIStatusError as exc:
        # Belt-and-suspenders: catch any other 529 that surfaces as
        # APIStatusError rather than the subclassed OverloadedError.
        if exc.status_code == 529:
            request_id = getattr(exc, "request_id", None)
            log.warning("chat_anthropic_overloaded_status", request_id=request_id)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Claude API is temporarily overloaded (HTTP 529). "
                    "Already retried 5 times via the SDK. "
                    f"Please wait a minute and try again. request_id: {request_id}"
                ),
            ) from exc
        log.error("chat_anthropic_api_error", status_code=exc.status_code, error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic API error (HTTP {exc.status_code}): {exc}",
        ) from exc
    except Exception as exc:
        log.error("planner_failed", error=str(exc), exc_type=type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail=f"planner failed ({type(exc).__name__}): {exc}",
        ) from exc

    return ChatResponse(
        final_text=result.final_text,
        events=[_event_to_dict(ev) for ev in result.events],
        history=result.messages,
        stop_reason=result.stop_reason,
        awaiting_approval=_pending_approval(result.events),
    )
