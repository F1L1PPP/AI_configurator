"""Regression tests for audit-B5 + B18 — EventBus back-pressure + subscriber cap.

B5: when a subscriber's queue is full, the oldest event is dropped to fit
    the new one. The drop is logged (so a slow client doesn't silently
    lose events without anyone noticing).
B18: subscriber count is capped — a misbehaving client looping on
     connect/disconnect can't grow the list unboundedly.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core.eventbus import EventBus, _safe_put

# ---------------------------------------------------------------------------
# _safe_put — back-pressure outcome reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_put_lands_in_queue_when_space_available():
    q: asyncio.Queue = asyncio.Queue(maxsize=3)
    assert _safe_put(q, {"type": "a"}) == "put"
    assert _safe_put(q, {"type": "b"}) == "put"
    assert q.qsize() == 2


@pytest.mark.asyncio
async def test_safe_put_displaces_oldest_when_full():
    """When a subscriber lags and the queue fills, the OLDEST event is
    dropped — slow UI clients should see the most recent events, not the
    stalest ones."""
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    _safe_put(q, {"type": "first"})
    _safe_put(q, {"type": "second"})
    outcome = _safe_put(q, {"type": "third"})

    assert outcome == "displaced"
    # First event was dropped; queue holds second + third.
    items = []
    while not q.empty():
        items.append((q.get_nowait())["type"])
    assert items == ["second", "third"]


# ---------------------------------------------------------------------------
# Subscriber cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_cap_blocks_runaway_subscribe():
    """A misbehaving client can't grow the subscriber list past MAX_SUBSCRIBERS.
    The Nth+1 subscribe raises RuntimeError so the WS route can close the
    incoming connection with a 'try again later' code."""
    bus = EventBus()
    queues = []
    try:
        for _ in range(bus.MAX_SUBSCRIBERS):
            queues.append(bus.subscribe())

        assert bus.subscriber_count() == bus.MAX_SUBSCRIBERS

        with pytest.raises(RuntimeError, match="subscriber cap"):
            bus.subscribe()
    finally:
        for q in queues:
            bus.unsubscribe(q)


@pytest.mark.asyncio
async def test_unsubscribe_frees_a_slot():
    """After we unsubscribe, a new subscribe must succeed — no leak."""
    bus = EventBus()
    queues = [bus.subscribe() for _ in range(bus.MAX_SUBSCRIBERS)]
    bus.unsubscribe(queues[0])

    # Now there's room again.
    new_q = bus.subscribe()
    assert bus.subscriber_count() == bus.MAX_SUBSCRIBERS

    for q in queues[1:] + [new_q]:
        bus.unsubscribe(q)


# ---------------------------------------------------------------------------
# Publish + back-pressure integration — drops are logged, not silent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_through_full_queue_displaces_and_keeps_publishing():
    """Even when a subscriber's queue is overflowing, publish keeps going.
    Slow consumers can't block the planner."""
    bus = EventBus(queue_size=2)
    q = bus.subscribe()
    try:
        # Publish 5 events; queue holds 2; older events are displaced.
        for i in range(5):
            bus.publish({"type": "tool_call", "n": i})

        # Let the loop drain the call_soon_threadsafe scheduling.
        await asyncio.sleep(0)

        # Queue should hold the 2 most recent.
        items = []
        while not q.empty():
            items.append(q.get_nowait()["n"])
        # The exact set depends on displace ordering but the size is capped.
        assert len(items) == 2
        # The newest event (n=4) must be present — slow clients see latest.
        assert 4 in items
    finally:
        bus.unsubscribe(q)
