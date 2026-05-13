"""GET /ws/agent — live stream of planner events to the frontend.

Subscribes to the in-process event bus and forwards each event as JSON.

Clients can connect at any time. Events published while no client is connected
are simply not delivered (no historical replay) — the synchronous /api/chat
response carries the full event trace for that turn, so anything missed in
real time can still be reconstructed from the response payload.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.eventbus import bus
from backend.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    await ws.accept()
    q = bus.subscribe()
    log.info("ws_agent_connected", subscribers=bus.subscriber_count())
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        log.info("ws_agent_disconnected")
    finally:
        bus.unsubscribe(q)
