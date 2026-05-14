"""Unit tests for backend.webui_agent.generic_driver.webui_open (Phase 4 slice 1).

Patches `WebUISession` so the tests don't spawn real Playwright children.
The session protocol itself is covered in `tests/unit/test_webui_subprocess.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.webui_agent import generic_driver
from backend.webui_agent._subprocess import SubprocessFlowError
from backend.webui_agent.generic_driver import (
    close_all_sessions,
    webui_open,
)

pytestmark = pytest.mark.webui


@pytest.fixture(autouse=True)
def _clean_sessions():
    """Reset the module-level session cache before and after every test."""
    generic_driver._sessions.clear()
    yield
    generic_driver._sessions.clear()


def _make_fake_session(open_reply: dict | None = None) -> MagicMock:
    sess = MagicMock()
    sess.is_alive.return_value = True
    sess.evidence_dir = "/tmp/evid"
    if open_reply is None:
        open_reply = {
            "ok": True,
            "view": {
                "view_id": "abc12345",
                "url": "https://lab/webui/#/general",
                "title": "General",
                "elements": [],
                "modals": [],
                "errors": [],
            },
        }
    sess.send.return_value = open_reply
    return sess


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_webui_open_creates_session_and_returns_view():
    fake_sess = _make_fake_session()

    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        return_value=fake_sess,
    ) as mock_cls:
        result = webui_open("/webui/#/general", action_id="act_001")

    # The session was constructed once with the right action_id.
    mock_cls.assert_called_once_with("act_001", headless=None)
    # An "open" op was sent through it.
    fake_sess.send.assert_called_once_with({"op": "open", "path": "/webui/#/general"})
    # The view shape comes back along with the session_id.
    assert result["session_id"] == "act_001"
    assert result["view"]["view_id"] == "abc12345"
    assert result["view"]["title"] == "General"


def test_webui_open_allocates_session_id_when_none_passed():
    fake_sess = _make_fake_session()

    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        return_value=fake_sess,
    ):
        result = webui_open("/webui/#/general")

    sid = result["session_id"]
    # `sess_<8 hex chars>` per the implementation.
    assert sid.startswith("sess_")
    assert len(sid) == 5 + 8


# ---------------------------------------------------------------------------
# Session reuse
# ---------------------------------------------------------------------------


def test_webui_open_reuses_existing_alive_session():
    fake_sess = _make_fake_session()

    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        return_value=fake_sess,
    ) as mock_cls:
        webui_open("/webui/#/general", action_id="act_002")
        # Second call with the same action_id should NOT spawn a new session.
        webui_open("/webui/#/vlan", action_id="act_002")

    # WebUISession constructor called exactly once.
    assert mock_cls.call_count == 1
    # Two send() ops though — open then open(vlan).
    assert fake_sess.send.call_count == 2


def test_webui_open_rebuilds_when_existing_session_is_dead():
    dead_sess = _make_fake_session()
    dead_sess.is_alive.return_value = False
    live_sess = _make_fake_session()

    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        side_effect=[dead_sess, live_sess],
    ) as mock_cls:
        # First call — creates the (soon-dead) session.
        webui_open("/webui/#/general", action_id="act_003")
        # Pretend it died between calls; second call must spawn a new one.
        webui_open("/webui/#/vlan", action_id="act_003")

    assert mock_cls.call_count == 2


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_webui_open_returns_init_failure_when_session_init_raises():
    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        side_effect=SubprocessFlowError(
            flow="webui_session_init",
            error="WebUI login failed",
            exc_type="RuntimeError",
            stderr="",
        ),
    ):
        result = webui_open("/webui/#/general", action_id="act_004")

    assert result["error"] == "session_init_failed"
    assert result["exc_type"] == "RuntimeError"
    assert "login" in result["message"].lower()
    assert result["session_id"] == "act_004"


def test_webui_open_returns_op_failure_when_send_raises():
    fake_sess = _make_fake_session()
    fake_sess.send.side_effect = SubprocessFlowError(
        flow="webui_session",
        error="timed out after 30.0s",
        exc_type="Timeout",
        stderr="",
    )

    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        return_value=fake_sess,
    ):
        result = webui_open("/webui/#/general", action_id="act_005")

    assert result["error"] == "webui_open_failed"
    assert result["exc_type"] == "Timeout"
    # Failed session was discarded; it should no longer be cached.
    assert "act_005" not in generic_driver._sessions


def test_webui_open_returns_op_failure_when_reply_not_ok():
    fake_sess = _make_fake_session(
        open_reply={
            "ok": False,
            "error": "Page didn't render in time",
            "exc_type": "PlaywrightTimeoutError",
        }
    )

    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        return_value=fake_sess,
    ):
        result = webui_open("/webui/#/general", action_id="act_006")

    assert result["error"] == "webui_open_failed"
    assert result["exc_type"] == "PlaywrightTimeoutError"
    # Session is kept alive — the planner can retry.
    assert "act_006" in generic_driver._sessions


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_close_all_sessions_closes_every_cached_session():
    sess_a = _make_fake_session()
    sess_b = _make_fake_session()

    with patch(
        "backend.webui_agent.generic_driver.WebUISession",
        side_effect=[sess_a, sess_b],
    ):
        webui_open("/p1", action_id="act_a")
        webui_open("/p2", action_id="act_b")

    assert len(generic_driver._sessions) == 2
    close_all_sessions()
    sess_a.close.assert_called_once()
    sess_b.close.assert_called_once()
    assert generic_driver._sessions == {}
