"""Thread-safe async pub/sub bus for agent events.

The planner runs inside `run_in_threadpool` (a worker thread) but WebSocket
consumers run on the asyncio event loop. `EventBus.publish` is safe to call
from either context — it schedules `put_nowait` on each subscriber's queue
via `loop.call_soon_threadsafe`.

Back-pressure: each subscriber has a bounded queue (default 256). When it
fills, the oldest event is dropped to make room for the new one. Dropping
the oldest is correct for our use case (a slow UI client) — we'd rather
the client see the *latest* events than block the planner.

Subscriber cap: a misbehaving frontend looping on connect/disconnect could
otherwise grow the subscriber list unboundedly. The MAX_SUBSCRIBERS limit
is enforced inside `subscribe()` — over the cap, the call raises
RuntimeError and the WS route closes with a 1013 (try-again-later) code.

Event shape (convention, not enforced):
    {"type": "tool_call", "ts": "2026-05-13T...", "data": {...}}
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _safe_put(q: asyncio.Queue, event: dict[str, Any]) -> str:
    """Best-effort put into a bounded queue.

    Returns one of:
      "put"       — landed in the queue normally
      "displaced" — queue was full, dropped the OLDEST event to fit this one
      "dropped"   — couldn't fit even after displacing (extremely rare;
                    happens only under heavy multi-publisher contention)
    """
    try:
        q.put_nowait(event)
        return "put"
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            q.get_nowait()
        try:
            q.put_nowait(event)
            return "displaced"
        except asyncio.QueueFull:
            return "dropped"


class EventBus:
    # Cap subscribers so a runaway client can't grow the list unboundedly.
    # 32 is more than enough for a single-operator workstation; if we ever
    # need more, raise the cap.
    MAX_SUBSCRIBERS = 32

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        """Register a subscriber. MUST be called from an asyncio context.

        Returns the asyncio.Queue the caller should consume from.

        Raises:
            RuntimeError: subscriber cap reached. Caller should close the
                          incoming connection with a "retry later" code.
        """
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
            if len(self._subscribers) >= self.MAX_SUBSCRIBERS:
                raise RuntimeError(
                    f"EventBus subscriber cap reached ({self.MAX_SUBSCRIBERS}); "
                    "refusing new subscription"
                )
            self._subscribers.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(loop, qq) for (loop, qq) in self._subscribers if qq is not q]

    def publish(self, event: dict[str, Any]) -> None:
        """Publish an event to every subscriber. Safe to call from any thread."""
        with self._lock:
            subs = list(self._subscribers)
        for loop, q in subs:
            # Loop may have been closed mid-publish — drop the event for that subscriber.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._put_and_log, q, event)

    def _put_and_log(self, q: asyncio.Queue, event: dict[str, Any]) -> None:
        """Invoked on the subscriber's loop. Logs back-pressure drops so a
        slow client doesn't silently lose events without anyone noticing."""
        outcome = _safe_put(q, event)
        if outcome != "put":
            log.warning(
                "eventbus_backpressure",
                outcome=outcome,
                event_type=event.get("type"),
                queue_size=q.qsize(),
            )

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()
