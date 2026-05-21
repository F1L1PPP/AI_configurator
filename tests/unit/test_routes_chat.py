"""Unit tests for POST /api/chat — focus on error-handling paths.

Uses FastAPI TestClient with run_planner mocked so no real Anthropic call
is made and no SSH connection is needed.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest  # noqa: F401 — pytest is used via fixture decorators
from anthropic._exceptions import OverloadedError as AnthropicOverloadedError
from fastapi.testclient import TestClient
from netmiko.exceptions import NetMikoTimeoutException

from backend.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_overloaded_error(request_id: str = "req_test_chat_529") -> AnthropicOverloadedError:
    """Build a real OverloadedError with a real httpx.Response so the SDK's
    APIStatusError.__init__ can access response.request, response.status_code,
    and response.headers."""
    mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    mock_response = httpx.Response(
        status_code=529,
        headers={"request-id": request_id},
        request=mock_request,
    )
    return AnthropicOverloadedError(
        message="Overloaded",
        response=mock_response,
        body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Chunk 10 — HTTP 503 on OverloadedError
# ---------------------------------------------------------------------------


def test_chat_returns_503_on_overloaded_error(client):
    """When run_planner raises OverloadedError, POST /api/chat must respond
    with HTTP 503 and a detail containing both 'overloaded' and 'request_id'."""
    err = _make_overloaded_error("req_test_chat_529")

    with patch("backend.api.routes_chat.run_planner", side_effect=err):
        response = client.post(
            "/api/chat",
            json={"message": "set hostname to TEST", "history": []},
        )

    assert response.status_code == 503
    detail = response.json().get("detail", "")
    assert "overloaded" in detail.lower()
    assert "request_id" in detail.lower()


# ---------------------------------------------------------------------------
# Chunk A — Session lifecycle hardening: close_all_sessions always called
# ---------------------------------------------------------------------------


def _stub_planner_result():
    """Minimal PlannerResult-shaped object for success-path tests."""
    from backend.orchestration.planner import PlannerResult

    return PlannerResult(final_text="ok", events=[], messages=[], stop_reason="end_turn")


def test_chat_closes_sessions_on_success(client):
    """Successful planner call → close_all_sessions must be invoked once."""
    with (
        patch("backend.api.routes_chat.run_planner", return_value=_stub_planner_result()),
        patch("backend.api.routes_chat.close_all_sessions") as mock_close,
    ):
        response = client.post("/api/chat", json={"message": "hi", "history": []})

    assert response.status_code == 200
    mock_close.assert_called_once()


def test_chat_closes_sessions_on_exception(client):
    """Planner raises → exception propagates as HTTP error AND close_all_sessions still called."""
    with (
        patch(
            "backend.api.routes_chat.run_planner",
            side_effect=NetMikoTimeoutException("router unreachable"),
        ),
        patch("backend.api.routes_chat.close_all_sessions") as mock_close,
    ):
        response = client.post("/api/chat", json={"message": "hi", "history": []})

    assert response.status_code == 503
    mock_close.assert_called_once()
