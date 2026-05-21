"""Unit tests for GET /ws/agent — origin gate and strict-origin toggle.

Tests focus on the access-control logic in ws_agent without exercising the
real EventBus or Anthropic calls.  Each test controls settings via a patch
on the routes_ws module's own reference to get_settings.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.settings import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws(origin: str | None = None) -> MagicMock:
    """Return a mock WebSocket whose headers optionally contain an Origin."""
    ws = MagicMock()
    ws.headers = {"origin": origin} if origin is not None else {}
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    # receive_text must be async so the main recv-loop doesn't crash.
    ws.receive_text = AsyncMock(side_effect=Exception("stop"))
    return ws


def _make_settings_mock(
    ws_strict_origin: bool, allowed_origins: list[str] | None = None
) -> MagicMock:
    mock = MagicMock()
    mock.ws_strict_origin = ws_strict_origin
    mock.allowed_origins = allowed_origins or [
        "http://localhost:3000",
        "http://localhost:8000",
    ]
    return mock


# ---------------------------------------------------------------------------
# Tests — missing origin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_agent_missing_origin_allowed_by_default() -> None:
    """ws_strict_origin=False (default): missing-origin → ws.accept() called.

    After accept(), the handler exits early because bus.subscribe raises
    RuntimeError (subscriber cap), which causes ws.close(1013).  The key
    assertion is that accept() was called — the origin gate did NOT reject.
    """
    from backend.api.routes_ws import ws_agent

    ws = _make_ws(origin=None)
    settings_mock = _make_settings_mock(ws_strict_origin=False)

    with (
        patch("backend.api.routes_ws.get_settings", return_value=settings_mock),
        patch("backend.api.routes_ws.bus") as mock_bus,
    ):
        mock_bus.subscribe.side_effect = RuntimeError("cap")
        await ws_agent(ws)

    ws.accept.assert_awaited_once()


@pytest.mark.asyncio
async def test_ws_agent_missing_origin_rejected_when_strict() -> None:
    """ws_strict_origin=True: missing-origin → ws.close(1008) called; accept NOT called."""
    from backend.api.routes_ws import ws_agent

    ws = _make_ws(origin=None)
    settings_mock = _make_settings_mock(ws_strict_origin=True)

    with patch("backend.api.routes_ws.get_settings", return_value=settings_mock):
        await ws_agent(ws)

    ws.accept.assert_not_awaited()
    ws.close.assert_awaited_once()
    # Must close with 1008 Policy Violation.
    call_kwargs = ws.close.call_args
    code = call_kwargs.kwargs.get("code") or (call_kwargs.args[0] if call_kwargs.args else None)
    assert code == 1008, f"Expected close code 1008, got {code}"
