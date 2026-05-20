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
