"""GET /ws/agent — live stream of planner events to the frontend.

Subscribes to the in-process event bus and forwards each event as JSON.

Clients can connect at any time. Events published while no client is connected
are simply not delivered (no historical replay) — the synchronous /api/chat
response carries the full event trace for that turn, so anything missed in
real time can still be reconstructed from the response payload.
"""

from __future__ import annotations

import asyncio
import contextlib

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

    async def pump_events() -> None:
        """Forward events from the bus queue to the socket.

        Runs as a background task while the main handler parks on
        receive_text() to detect disconnect. Swallows WebSocketDisconnect
        and RuntimeError from send_json after the socket is closed — the
        main task is the source of truth for the disconnect signal.
        """
        try:
            while True:
                event = await q.get()
                await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            return

    pump = asyncio.create_task(pump_events())
    try:
        # Park here so an idle client disconnect surfaces immediately.
        # Frontend doesn't send messages, so receive_text() blocks until
        # the client closes the socket, then raises WebSocketDisconnect.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        bus.unsubscribe(q)
        log.info("ws_agent_disconnected", subscribers=bus.subscriber_count())
