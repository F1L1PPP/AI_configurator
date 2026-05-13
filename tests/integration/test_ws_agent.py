"""Integration test for the /ws/agent WebSocket route.

Uses Starlette's TestClient (synchronous wrapper) to connect via WebSocket,
then publishes events on the bus from the test thread and asserts they
arrive on the socket.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.core.eventbus import bus
from backend.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_ws_agent_forwards_published_event(client: TestClient) -> None:
    with client.websocket_connect("/ws/agent") as ws:
        # Give the WS handler time to subscribe.
        for _ in range(50):
            if bus.subscriber_count() >= 1:
                break
            time.sleep(0.01)
        assert bus.subscriber_count() >= 1

        bus.publish(
            {"type": "tool_call", "ts": "2026-05-13T00:00:00Z", "data": {"name": "show_version"}}
        )
        event = ws.receive_json()
        assert event["type"] == "tool_call"
        assert event["data"]["name"] == "show_version"


def test_ws_agent_publish_from_worker_thread(client: TestClient) -> None:
    with client.websocket_connect("/ws/agent") as ws:
        for _ in range(50):
            if bus.subscriber_count() >= 1:
                break
            time.sleep(0.01)

        def publish_in_thread() -> None:
            bus.publish(
                {"type": "applied", "ts": "2026-05-13T00:00:00Z", "data": {"action_id": "act_42"}}
            )

        t = threading.Thread(target=publish_in_thread)
        t.start()
        t.join()

        event = ws.receive_json()
        assert event["type"] == "applied"
        assert event["data"]["action_id"] == "act_42"


def test_ws_agent_unsubscribes_on_disconnect(client: TestClient) -> None:
    before = bus.subscriber_count()
    with client.websocket_connect("/ws/agent"):
        for _ in range(50):
            if bus.subscriber_count() > before:
                break
            time.sleep(0.01)
        assert bus.subscriber_count() == before + 1
    # After exit, the with-block closes the connection — give the cleanup a tick.
    for _ in range(50):
        if bus.subscriber_count() == before:
            break
        time.sleep(0.01)
    assert bus.subscriber_count() == before
