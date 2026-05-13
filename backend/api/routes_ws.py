"""GET /ws/agent — live stream of planner events to the frontend.

Subscribes to the in-process event bus and forwards each event as JSON.

Clients can connect at any time. Events published while no client is connected
are simply not delivered (no historical replay) — the synchronous /api/chat
response carries the full event trace for that turn, so anything missed in
real time can still be reconstructed from the response payload.

Origin allowlist: WebSockets bypass the browser's CORS policy (the upgrade
handshake is a regular HTTP request that browsers don't subject to
Access-Control-* checks), so this route enforces the same origin list
configured for CORS itself (`settings.allowed_origins`). Without this gate,
a page on any origin could open ws://localhost:8000/ws/agent and read
every planner event in the workstation's session — including tool inputs.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.eventbus import bus
from backend.core.logging import get_logger
from backend.core.settings import get_settings

log = get_logger(__name__)
router = APIRouter()

# Close codes (RFC 6455 + WebSocket standard ranges)
_CLOSE_POLICY_VIOLATION = 1008  # origin rejected
_CLOSE_TRY_AGAIN_LATER = 1013  # subscriber cap reached


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    # Enforce the same allowlist CORS uses for HTTP. WebSocket upgrades
    # carry an Origin header in the handshake when initiated by a
    # browser; non-browser clients (curl, TestClient, custom scripts)
    # generally omit it.
    #
    # Policy:
    #  - Origin missing  → allowed (covers TestClient + local debugging
    #                     with curl --include-header). Logged at INFO
    #                     so a sudden surge of non-browser connects is
    #                     visible in the audit log.
    #  - Origin foreign  → rejected with 1008 Policy Violation, the
    #                     equivalent of a CORS preflight failure.
    #  - Origin allowed  → proceeds normally.
    #
    # If we later deploy beyond localhost, flip this to strict mode
    # (reject missing OR foreign) and add a settings.ws_strict_origin
    # toggle so the dev workflow doesn't break.
    origin = ws.headers.get("origin", "")
    allowed = set(get_settings().allowed_origins)
    if origin and origin not in allowed:
        log.warning("ws_agent_origin_rejected", origin=origin)
        await ws.close(code=_CLOSE_POLICY_VIOLATION, reason="origin not allowed")
        return
    if not origin:
        log.info("ws_agent_origin_missing", note="non-browser client or local debug tool")

    await ws.accept()

    try:
        q = bus.subscribe()
    except RuntimeError as exc:
        # Bus subscriber cap — tell the client to retry later.
        log.warning("ws_agent_subscriber_cap", error=str(exc))
        await ws.close(code=_CLOSE_TRY_AGAIN_LATER, reason="server at capacity")
        return

    log.info("ws_agent_connected", subscribers=bus.subscriber_count())

    async def pump_events() -> None:
        """Forward events from the bus queue to the socket.

        Catches the narrow set of expected disconnect errors. Other
        exceptions propagate so the outer finally can log them — silent
        swallow on `Exception` (the previous behavior) hid real bugs in
        the queue or in send_json.
        """
        try:
            while True:
                event = await q.get()
                await ws.send_json(event)
        except WebSocketDisconnect:
            # Main task is source of truth for the disconnect signal.
            return
        except RuntimeError as exc:
            # send_json raises RuntimeError on closed socket — same intent
            # as WebSocketDisconnect; treat as graceful exit. Re-raise
            # anything that doesn't look like a closed-socket error so the
            # outer finally logs it.
            msg = str(exc).lower()
            if any(token in msg for token in ("close", "disconnect", "not connected")):
                return
            raise

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
        try:
            await pump
        except asyncio.CancelledError:
            # Expected: we just cancelled the task.
            pass
        except Exception as exc:
            # Anything else is a real bug — log loudly. Don't re-raise:
            # the connection is already shutting down, and uvicorn doesn't
            # care about exceptions from a WebSocket handler at this stage.
            log.error(
                "ws_agent_pump_failed",
                error=str(exc),
                exc_type=type(exc).__name__,
                exc_info=True,
            )
        with contextlib.suppress(Exception):
            bus.unsubscribe(q)
        log.info("ws_agent_disconnected", subscribers=bus.subscriber_count())
