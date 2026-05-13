"""Thread-safe async pub/sub bus for agent events.

The planner runs inside `run_in_threadpool` (a worker thread) but WebSocket
consumers run on the asyncio event loop. `EventBus.publish` is safe to call
from either context — it schedules `put_nowait` on each subscriber's queue
via `loop.call_soon_threadsafe`.

Back-pressure: each subscriber has a bounded queue (default 256). When it
fills, the oldest event is dropped to make room for the new one. Dropping
the oldest is correct for our use case (a slow UI client) — we'd rather
the client see the *latest* events than block the planner.

Event shape (convention, not enforced):
    {"type": "tool_call", "ts": "2026-05-13T...", "data": {...}}
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any


def _safe_put(q: asyncio.Queue, event: dict[str, Any]) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            q.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(event)


class EventBus:
    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        """Register a subscriber. MUST be called from an asyncio context.

        Returns the asyncio.Queue the caller should consume from.
        """
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
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
                loop.call_soon_threadsafe(_safe_put, q, event)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()
