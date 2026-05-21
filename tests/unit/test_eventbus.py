"""Unit tests for the thread-safe event bus."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

import backend.core.eventbus as eventbus_mod
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


# ---------------------------------------------------------------------------
# Back-pressure log throttle — chunk D polish batch (#11)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_bp_throttle_state():
    """Reset module-level throttle state before every test in this module."""
    eventbus_mod._bp_last_log_time = 0.0
    eventbus_mod._bp_drops_since_log = 0
    yield
    eventbus_mod._bp_last_log_time = 0.0
    eventbus_mod._bp_drops_since_log = 0


def test_eventbus_backpressure_logs_first_drop_immediately() -> None:
    """The very first drop fires a log immediately.

    Calls _put_and_log directly (bypassing call_soon_threadsafe) so we can
    safely patch time.monotonic without breaking asyncio's own timer.
    Verifies by inspecting module-level throttle state after the call.
    """
    b = EventBus(queue_size=1)
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    # Manually fill the queue so _put_and_log sees a back-pressure outcome.
    q.put_nowait({"type": "fill"})

    with patch("backend.core.eventbus.time.monotonic", return_value=10.0):
        b._put_and_log(q, {"type": "overflow"})

    # Log fired → counter reset to 0 and timestamp updated to 10.0.
    assert eventbus_mod._bp_last_log_time == 10.0
    assert eventbus_mod._bp_drops_since_log == 0


def test_eventbus_backpressure_throttles_subsequent_drops() -> None:
    """Multiple drops within the 5 s window produce only one log; a drop after
    the window produces a second log with the aggregated count.

    Calls _put_and_log directly to keep time.monotonic patch isolated from
    asyncio internals.
    """
    b = EventBus(queue_size=1)
    q: asyncio.Queue = asyncio.Queue(maxsize=1)

    # time_seq: drop1=10.0 (logs, resets), drop2=11.0 (suppressed),
    #           drop3=12.0 (suppressed), drop4=20.0 (logs aggregated=3).
    time_seq = [10.0, 11.0, 12.0, 20.0]

    with patch("backend.core.eventbus.time.monotonic", side_effect=time_seq):
        # Drop 1 — queue empty → "put" (not a drop). Pre-fill first.
        q.put_nowait({"type": "fill"})
        # Drop 1 — monotonic()=10.0; 10.0-0.0=10>=5 → logs, resets counter.
        b._put_and_log(q, {"type": "drop1"})
        assert eventbus_mod._bp_last_log_time == 10.0
        assert eventbus_mod._bp_drops_since_log == 0

        # Drop 2 — monotonic()=11.0; 11.0-10.0=1<5 → suppressed.
        b._put_and_log(q, {"type": "drop2"})
        assert eventbus_mod._bp_drops_since_log == 1  # counted but not logged

        # Drop 3 — monotonic()=12.0; 12.0-10.0=2<5 → suppressed.
        b._put_and_log(q, {"type": "drop3"})
        assert eventbus_mod._bp_drops_since_log == 2

        # Drop 4 — monotonic()=20.0; 20.0-10.0=10>=5 → logs aggregated=3, resets.
        b._put_and_log(q, {"type": "drop4"})
        assert eventbus_mod._bp_last_log_time == 20.0
        assert eventbus_mod._bp_drops_since_log == 0
