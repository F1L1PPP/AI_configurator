"""Unit tests for the thread-safe event bus."""

from __future__ import annotations

import asyncio
import threading

import pytest

from backend.core.eventbus import EventBus


@pytest.mark.asyncio
async def test_subscribe_returns_queue() -> None:
    b = EventBus()
    q = b.subscribe()
    assert isinstance(q, asyncio.Queue)
    assert b.subscriber_count() == 1
    b.unsubscribe(q)
    assert b.subscriber_count() == 0


@pytest.mark.asyncio
async def test_publish_from_same_thread_delivers_event() -> None:
    b = EventBus()
    q = b.subscribe()
    b.publish({"type": "agent_thinking", "data": {"i": 1}})
    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event == {"type": "agent_thinking", "data": {"i": 1}}


@pytest.mark.asyncio
async def test_publish_from_worker_thread_delivers_event() -> None:
    b = EventBus()
    q = b.subscribe()

    def publish_in_thread() -> None:
        b.publish({"type": "tool_call", "data": {"name": "show_version"}})

    t = threading.Thread(target=publish_in_thread)
    t.start()
    t.join()

    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event["type"] == "tool_call"
    assert event["data"]["name"] == "show_version"


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers() -> None:
    b = EventBus()
    q1 = b.subscribe()
    q2 = b.subscribe()
    b.publish({"type": "applied", "data": {"action_id": "act_1"}})

    e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert e1 == e2


@pytest.mark.asyncio
async def test_unsubscribed_queue_does_not_receive() -> None:
    b = EventBus()
    q1 = b.subscribe()
    q2 = b.subscribe()
    b.unsubscribe(q1)
    b.publish({"type": "verified", "data": {}})
    e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert e2["type"] == "verified"
    assert q1.qsize() == 0


@pytest.mark.asyncio
async def test_backpressure_drops_oldest_on_queue_full() -> None:
    b = EventBus(queue_size=4)
    q = b.subscribe()
    for i in range(10):
        b.publish({"type": "tool_call", "data": {"i": i}})
    # Give the event loop a tick so call_soon_threadsafe callbacks run.
    await asyncio.sleep(0.05)
    received = []
    while not q.empty():
        received.append(q.get_nowait())
    assert len(received) == 4
    # We should have the last 4 events (oldest dropped).
    seen_i = [e["data"]["i"] for e in received]
    assert seen_i == sorted(seen_i)
    assert max(seen_i) == 9
