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
    webui_describe_page,
    webui_open,
    webui_open_form_for_planning,
    webui_verify,
)

pytestmark = pytest.mark.webui


@pytest.fixture(autouse=True)
def _clean_sessions():
    """Reset module-level state before and after every test.

    ``_sessions``, ``_pre_snapshotted``, and ``_vision_eid_failures`` are
    all cleared so test order (including pytest --random-order) cannot
    leak state across tests.
    """
    generic_driver._sessions.clear()
    generic_driver._pre_snapshotted.clear()
    generic_driver._vision_eid_failures.clear()
    yield
    generic_driver._sessions.clear()
    generic_driver._pre_snapshotted.clear()
    generic_driver._vision_eid_failures.clear()


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


# ---------------------------------------------------------------------------
# webui_describe_page (Phase 4 slice 2 commit 1)
# ---------------------------------------------------------------------------


def test_webui_describe_page_returns_fresh_view():
    fake_sess = _make_fake_session()
    fake_sess.send.return_value = {
        "ok": True,
        "view": {
            "view_id": "fresh999",
            "url": "https://lab/webui/#/general",
            "title": "General",
            "elements": [],
            "modals": [],
            "errors": [],
        },
    }
    generic_driver._sessions["sess_X"] = fake_sess

    result = webui_describe_page("sess_X")

    fake_sess.send.assert_called_once_with({"op": "describe"})
    assert result["session_id"] == "sess_X"
    assert result["view"]["view_id"] == "fresh999"


def test_webui_describe_page_session_not_found():
    # _sessions is empty (autouse fixture); the session_id is bogus.
    result = webui_describe_page("nonexistent")

    assert result["error"] == "session_not_found"
    assert result["session_id"] == "nonexistent"


def test_webui_describe_page_propagates_subprocess_error():
    fake_sess = _make_fake_session()
    fake_sess.send.side_effect = SubprocessFlowError(
        flow="webui_session",
        error="timed out after 30.0s",
        exc_type="Timeout",
        stderr="",
    )
    generic_driver._sessions["sess_Y"] = fake_sess

    result = webui_describe_page("sess_Y")

    assert result["error"] == "webui_describe_failed"
    assert result["exc_type"] == "Timeout"
    # Crashed session is removed so the next call rebuilds clean.
    assert "sess_Y" not in generic_driver._sessions


# ---------------------------------------------------------------------------
# webui_verify (Phase 4 slice 2 commit 1)
# ---------------------------------------------------------------------------


def test_webui_verify_present_true():
    fake_sess = _make_fake_session()
    fake_sess.send.return_value = {
        "ok": True,
        "present": True,
        "url": "https://lab/webui/#/general",
    }
    generic_driver._sessions["sess_Z"] = fake_sess

    result = webui_verify("sess_Z", "VLAN 46 created")

    fake_sess.send.assert_called_once_with({"op": "verify", "text": "VLAN 46 created"})
    assert result["present"] is True
    assert result["url"] == "https://lab/webui/#/general"
    assert result["session_id"] == "sess_Z"


def test_webui_verify_propagates_subprocess_error():
    fake_sess = _make_fake_session()
    fake_sess.send.side_effect = SubprocessFlowError(
        flow="webui_session",
        error="subprocess closed stdout unexpectedly",
        exc_type="UnexpectedEOF",
        stderr="",
    )
    generic_driver._sessions["sess_W"] = fake_sess

    result = webui_verify("sess_W", "anything")

    assert result["error"] == "webui_verify_failed"
    assert result["exc_type"] == "UnexpectedEOF"
    # Session removed so retries rebuild.
    assert "sess_W" not in generic_driver._sessions


# ---------------------------------------------------------------------------
# Pre-snapshot move (Phase 4 slice 2 commit 1)
# ---------------------------------------------------------------------------


def test_webui_open_takes_pre_snapshot_first_time_only():
    fake_sess = _make_fake_session()

    with (
        patch(
            "backend.webui_agent.generic_driver.WebUISession",
            return_value=fake_sess,
        ),
        patch("backend.webui_agent.generic_driver.take_snapshot") as mock_snap,
    ):
        # First webui_open for action_id="act_PS1" — pre-snap taken.
        webui_open("/webui/#/general", action_id="act_PS1")
        # Second call with same action_id (session reused) — NOT taken again.
        webui_open("/webui/#/vlan", action_id="act_PS1")

    mock_snap.assert_called_once_with("act_PS1", "pre")


def test_webui_open_skips_pre_snapshot_without_action_id():
    fake_sess = _make_fake_session()

    with (
        patch(
            "backend.webui_agent.generic_driver.WebUISession",
            return_value=fake_sess,
        ),
        patch("backend.webui_agent.generic_driver.take_snapshot") as mock_snap,
    ):
        # No action_id — caller is a throwaway read-only navigation.
        webui_open("/webui/#/general")

    mock_snap.assert_not_called()


def test_webui_open_continues_when_pre_snapshot_raises():
    fake_sess = _make_fake_session()

    with (
        patch(
            "backend.webui_agent.generic_driver.WebUISession",
            return_value=fake_sess,
        ),
        patch(
            "backend.webui_agent.generic_driver.take_snapshot",
            side_effect=RuntimeError("SSH boom"),
        ),
    ):
        # Pre-snap blowing up must NOT abort the WebUI flow — pre-snap is
        # evidence, not a precondition.
        result = webui_open("/webui/#/general", action_id="act_PS2")

    assert "view" in result
    assert "error" not in result


# ---------------------------------------------------------------------------
# webui_act (Phase 4 slice 2 commit 2) — HITL-gated write tool
# ---------------------------------------------------------------------------


@pytest.fixture
def _act_patches():
    """Stack the four patches webui_act needs.

    is_approved → True by default; tests override per-case.
    mark_failed / pool / get_settings → no-op MagicMocks the tests can
    assert against.
    """
    with (
        patch("backend.webui_agent.generic_driver.is_approved") as mock_approved,
        patch("backend.webui_agent.generic_driver.mark_failed") as mock_failed,
        patch("backend.webui_agent.generic_driver.pool") as mock_pool,
        patch("backend.webui_agent.generic_driver.get_settings") as mock_settings,
    ):
        mock_approved.return_value = True
        fake_settings = MagicMock()
        fake_settings.router_host = "192.168.10.1"
        fake_settings.router_ssh_user = "cisco"
        mock_settings.return_value = fake_settings
        yield {
            "is_approved": mock_approved,
            "mark_failed": mock_failed,
            "pool": mock_pool,
            "settings": mock_settings,
        }


def _act_session(act_reply: dict) -> MagicMock:
    sess = _make_fake_session()
    sess.send.return_value = act_reply
    return sess


def test_webui_act_happy_path(_act_patches):
    from backend.webui_agent.generic_driver import webui_act

    fake_view = {"view_id": "post123", "elements": []}
    sess = _act_session({"ok": True, "view": fake_view, "attempts": 0})
    generic_driver._sessions["sess_A"] = sess

    result = webui_act(
        session_id="sess_A",
        view_id="pre123",
        eid="e_001",
        action="click",
        action_id="act_X",
    )

    sess.send.assert_called_once_with(
        {
            "op": "act",
            "view_id": "pre123",
            "eid": "e_001",
            "action": "click",
            "value": None,
        }
    )
    assert result["ok"] is True
    assert result["view"]["view_id"] == "post123"
    # Pool invalidated so next CLI tool sees a fresh SSH connection.
    _act_patches["pool"].invalidate.assert_called_once_with("192.168.10.1", "cisco")
    # NOT marked failed (happy path).
    _act_patches["mark_failed"].assert_not_called()


def test_webui_act_refuses_without_approval(_act_patches):
    from backend.webui_agent.generic_driver import webui_act

    _act_patches["is_approved"].return_value = False
    sess = _act_session({"ok": True, "view": {}})
    generic_driver._sessions["sess_B"] = sess

    result = webui_act(
        session_id="sess_B",
        view_id="v",
        eid="e_001",
        action="click",
        action_id="act_unapproved",
    )

    assert result["error"] == "not_approved"
    # Sub-process was NEVER asked to act.
    sess.send.assert_not_called()
    # mark_failed should NOT fire on a HITL refusal (state unchanged).
    _act_patches["mark_failed"].assert_not_called()
    _act_patches["pool"].invalidate.assert_not_called()


def test_webui_act_surfaces_stale_view_without_mark_failed(_act_patches):
    from backend.webui_agent.generic_driver import webui_act

    sess = _act_session(
        {
            "ok": False,
            "failure_reason": "stale_view",
            "view": {"view_id": "fresh"},
            "attempts": 0,
        }
    )
    generic_driver._sessions["sess_C"] = sess

    result = webui_act(
        session_id="sess_C",
        view_id="old",
        eid="e_001",
        action="click",
        action_id="act_X",
    )

    assert result["ok"] is False
    assert result["failure_reason"] == "stale_view"
    # Soft failure — action stays retryable.
    _act_patches["mark_failed"].assert_not_called()
    _act_patches["pool"].invalidate.assert_not_called()


@pytest.mark.parametrize(
    "failure_reason",
    [
        "element_missing",
        "element_hidden",
        "element_disabled",
        "element_intercepted",
        "click_timeout_unsafe_retry",
        "unknown_eid",
    ],
)
def test_webui_act_surfaces_soft_failures(_act_patches, failure_reason):
    from backend.webui_agent.generic_driver import webui_act

    sess = _act_session(
        {
            "ok": False,
            "failure_reason": failure_reason,
            "view": {"view_id": "v"},
            "attempts": 0,
        }
    )
    generic_driver._sessions["sess_D"] = sess

    result = webui_act(
        session_id="sess_D",
        view_id="v",
        eid="e_001",
        action="click",
        action_id="act_X",
    )

    assert result["ok"] is False
    assert result["failure_reason"] == failure_reason
    # All soft failures preserve action state for planner retry.
    _act_patches["mark_failed"].assert_not_called()
    _act_patches["pool"].invalidate.assert_not_called()


def test_webui_act_marks_failed_on_subprocess_error(_act_patches):
    from backend.webui_agent.generic_driver import webui_act

    sess = _make_fake_session()
    sess.send.side_effect = SubprocessFlowError(
        flow="webui_session",
        error="timed out after 30.0s",
        exc_type="Timeout",
        stderr="",
    )
    generic_driver._sessions["sess_E"] = sess

    result = webui_act(
        session_id="sess_E",
        view_id="v",
        eid="e_001",
        action="click",
        action_id="act_crash",
    )

    assert result["error"] == "webui_act_failed"
    assert result["exc_type"] == "Timeout"
    # Subprocess crash IS a hard failure — state must transition.
    _act_patches["mark_failed"].assert_called_once_with("act_crash")
    # Failed session removed so next call rebuilds clean.
    assert "sess_E" not in generic_driver._sessions


def test_webui_act_session_not_found_marks_failed(_act_patches):
    from backend.webui_agent.generic_driver import webui_act

    # _sessions empty — session_id never seen by webui_open.

    result = webui_act(
        session_id="nonexistent",
        view_id="v",
        eid="e_001",
        action="click",
        action_id="act_no_session",
    )

    assert result["error"] == "session_not_found"
    # No session = unrecoverable; hard failure.
    _act_patches["mark_failed"].assert_called_once_with("act_no_session")


def test_webui_act_multi_act_does_not_self_lockout(_act_patches):
    """Two acts with the same action_id must both pass HITL.

    Regression guard for the Phase 4 design-critique concern: if a future
    refactor calls mark_executed inside webui_act, the second act in a
    multi-act flow would see state == EXECUTED and be refused. Slice 2
    deliberately does NOT call mark_executed (Phase 5 owns it).
    """
    from backend.webui_agent.generic_driver import webui_act

    sess = _act_session({"ok": True, "view": {"view_id": "v"}, "attempts": 0})
    generic_driver._sessions["sess_F"] = sess

    # Act 1
    r1 = webui_act(
        session_id="sess_F",
        view_id="v",
        eid="e_001",
        action="click",
        action_id="act_multi",
    )
    # Act 2 — same action_id
    r2 = webui_act(
        session_id="sess_F",
        view_id="v",
        eid="e_002",
        action="fill",
        action_id="act_multi",
        value="LAB-R5",
    )

    assert r1["ok"] is True
    assert r2["ok"] is True
    # is_approved called for BOTH acts — neither was self-blocked.
    assert _act_patches["is_approved"].call_count == 2


# ---------------------------------------------------------------------------
# webui_act_by_intent (Phase 4 slice 2 commit 3)
# ---------------------------------------------------------------------------


def test_webui_act_by_intent_happy_path(_act_patches):
    from backend.webui_agent.generic_driver import webui_act_by_intent

    fake_view = {"view_id": "post_intent", "elements": []}
    sess = _act_session(
        {
            "ok": True,
            "view": fake_view,
            "chosen_eid": "e_007",
            "attempts": 0,
        }
    )
    generic_driver._sessions["sess_INT"] = sess

    intent = {"role": "button", "name": "Apply", "action": "click", "value": None}
    result = webui_act_by_intent(
        session_id="sess_INT",
        intent=intent,
        action_id="act_INT",
    )

    sess.send.assert_called_once_with({"op": "act_by_intent", "intent": intent})
    assert result["ok"] is True
    assert result["chosen_eid"] == "e_007"
    _act_patches["pool"].invalidate.assert_called_once_with("192.168.10.1", "cisco")
    _act_patches["mark_failed"].assert_not_called()


def test_webui_act_by_intent_unknown_eid_soft_failure(_act_patches):
    from backend.webui_agent.generic_driver import webui_act_by_intent

    sess = _act_session(
        {
            "ok": False,
            "failure_reason": "unknown_eid",
            "chosen_eid": None,
            "view": {"view_id": "v"},
            "attempts": 0,
        }
    )
    generic_driver._sessions["sess_INT2"] = sess

    result = webui_act_by_intent(
        session_id="sess_INT2",
        intent={"role": "button", "name": "Mystery", "action": "click"},
        action_id="act_INT2",
    )

    assert result["ok"] is False
    assert result["failure_reason"] == "unknown_eid"
    assert result["chosen_eid"] is None
    # Soft failure — action stays retryable.
    _act_patches["mark_failed"].assert_not_called()
    _act_patches["pool"].invalidate.assert_not_called()


def test_webui_act_by_intent_refuses_without_approval(_act_patches):
    from backend.webui_agent.generic_driver import webui_act_by_intent

    _act_patches["is_approved"].return_value = False
    sess = _act_session({"ok": True, "view": {}})
    generic_driver._sessions["sess_INT3"] = sess

    result = webui_act_by_intent(
        session_id="sess_INT3",
        intent={"role": "button", "name": "Apply", "action": "click"},
        action_id="act_unapproved_intent",
    )

    assert result["error"] == "not_approved"
    sess.send.assert_not_called()
    _act_patches["mark_failed"].assert_not_called()


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


# ---------------------------------------------------------------------------
# Vision-eid eviction — no-retry-on-vision-element_missing behavior
# ---------------------------------------------------------------------------


def test_webui_act_evicts_vision_eid_on_element_missing(_act_patches):
    """When a vision eid fails element_missing, it must be recorded so any
    subsequent call with the same (session_id, eid) is refused immediately
    without a child round-trip.
    """
    from backend.webui_agent.generic_driver import webui_act

    vision_eid = "vision_526b1241"
    sess = _act_session(
        {
            "ok": False,
            "failure_reason": "element_missing",
            "view": {"view_id": "v"},
            "attempts": 0,
        }
    )
    generic_driver._sessions["sess_VIS1"] = sess

    # First call — fails element_missing; should evict the vision eid.
    result = webui_act(
        session_id="sess_VIS1",
        view_id="v",
        eid=vision_eid,
        action="fill",
        action_id="act_V1",
        value="192.168.1.1",
    )

    assert result["ok"] is False
    assert result["failure_reason"] == "element_missing"
    # Eid must have been recorded in the eviction registry.
    assert vision_eid in generic_driver._vision_eid_failures.get("sess_VIS1", set())


def test_webui_act_refuses_evicted_vision_eid_immediately(_act_patches):
    """A pre-evicted vision eid must be refused without calling sess.send.

    Specifically: zero child round-trips, zero Playwright timeout budget
    consumed. The failure_reason must be 'element_missing' so the
    convergence guard in tool_registry.py trips on the same signal.
    """
    from backend.webui_agent.generic_driver import webui_act

    vision_eid = "vision_526b1241"
    sess = _act_session({"ok": True, "view": {"view_id": "v"}, "attempts": 0})
    generic_driver._sessions["sess_VIS2"] = sess

    # Pre-seed the eviction registry as if a prior call already evicted it.
    generic_driver._vision_eid_failures["sess_VIS2"] = {vision_eid}

    result = webui_act(
        session_id="sess_VIS2",
        view_id="v",
        eid=vision_eid,
        action="fill",
        action_id="act_V2",
        value="192.168.1.1",
    )

    assert result["ok"] is False
    assert result["failure_reason"] == "element_missing"
    # THE CRUCIAL ASSERTION — child was never contacted.
    sess.send.assert_not_called()
    _act_patches["mark_failed"].assert_not_called()


def test_webui_act_does_not_evict_non_vision_eid_on_element_missing(_act_patches):
    """Stable numeric eids (e_NNN) must NOT be evicted on element_missing.

    They are ARIA-tree rooted and survive most DOM mutations; evicting
    them would prematurely kill valid retry paths.
    """
    from backend.webui_agent.generic_driver import webui_act

    stable_eid = "e_003"
    sess = _act_session(
        {
            "ok": False,
            "failure_reason": "element_missing",
            "view": {"view_id": "v"},
            "attempts": 0,
        }
    )
    generic_driver._sessions["sess_VIS3"] = sess

    result = webui_act(
        session_id="sess_VIS3",
        view_id="v",
        eid=stable_eid,
        action="fill",
        action_id="act_V3",
        value="X",
    )

    assert result["ok"] is False
    assert result["failure_reason"] == "element_missing"
    # Stable eid must NOT appear in the eviction registry.
    assert stable_eid not in generic_driver._vision_eid_failures.get("sess_VIS3", set())


def test_webui_act_by_intent_evicts_vision_eid_on_element_missing(_act_patches):
    """webui_act_by_intent must also evict the chosen_eid when it is a vision
    eid and the child reports element_missing.
    """
    from backend.webui_agent.generic_driver import webui_act_by_intent

    vision_eid = "vision_aabbccdd"
    sess = _act_session(
        {
            "ok": False,
            "failure_reason": "element_missing",
            "chosen_eid": vision_eid,
            "view": {"view_id": "v"},
            "attempts": 0,
        }
    )
    generic_driver._sessions["sess_VIS4"] = sess

    result = webui_act_by_intent(
        session_id="sess_VIS4",
        intent={"role": "textbox", "name": "IP Address", "action": "fill", "value": "10.0.0.1"},
        action_id="act_V4",
    )

    assert result["ok"] is False
    assert result["failure_reason"] == "element_missing"
    assert result["chosen_eid"] == vision_eid
    # Vision eid must be recorded for future eviction.
    assert vision_eid in generic_driver._vision_eid_failures.get("sess_VIS4", set())


def test_webui_act_eviction_is_scoped_to_session(_act_patches):
    """An evicted vision eid in sess_A must NOT block the same eid in sess_B.

    Each WebUI session gets its own locator tree; the same vision eid
    string in a different session refers to a different DOM element.
    """
    from backend.webui_agent.generic_driver import webui_act

    vision_eid = "vision_11223344"
    # sess_A: eid is evicted
    generic_driver._vision_eid_failures["sess_A"] = {vision_eid}

    sess_b = _act_session({"ok": True, "view": {"view_id": "v"}, "attempts": 0})
    generic_driver._sessions["sess_B"] = sess_b

    result = webui_act(
        session_id="sess_B",
        view_id="v",
        eid=vision_eid,
        action="fill",
        action_id="act_V5",
        value="X",
    )

    # sess_B must not be affected by sess_A's eviction.
    assert result["ok"] is True
    sess_b.send.assert_called_once()


# ---------------------------------------------------------------------------
# webui_open_form_for_planning — HITL-safe propose-time helper
# ---------------------------------------------------------------------------


def _make_form_open_session(reply: dict) -> MagicMock:
    sess = MagicMock()
    sess.is_alive.return_value = True
    sess.send.return_value = reply
    return sess


def test_open_form_for_planning_happy_path():
    """Click lands, op=act_by_intent is sent, view returned."""
    post_view = {
        "view_id": "form_open_view",
        "elements": [{"role": "textbox", "name": "Pool Name"}],
    }
    sess = _make_form_open_session({"ok": True, "view": post_view})
    generic_driver._sessions["sess_plan"] = sess

    intent = {"role": "button", "name": "Add", "action": "click"}
    result = webui_open_form_for_planning("sess_plan", intent)

    assert result["ok"] is True
    assert result["view"]["view_id"] == "form_open_view"
    assert result["session_id"] == "sess_plan"
    # Must send act_by_intent (not act or open).
    sess.send.assert_called_once_with({"op": "act_by_intent", "intent": intent})


def test_open_form_for_planning_non_click_refused():
    """Any non-click action is rejected without touching the session."""
    sess = _make_form_open_session({"ok": True, "view": {}})
    generic_driver._sessions["sess_noclick"] = sess

    result = webui_open_form_for_planning(
        "sess_noclick", {"role": "textbox", "name": "Pool Name", "action": "fill", "value": "x"}
    )

    assert result["error"] == "non_click_refused"
    # Session must NOT be touched — helper enforces read-only at the Python level.
    sess.send.assert_not_called()


def test_open_form_for_planning_session_not_found():
    """Unknown session_id returns session_not_found error."""
    result = webui_open_form_for_planning(
        "sess_ghost", {"role": "button", "name": "+", "action": "click"}
    )
    assert result["error"] == "session_not_found"


def test_open_form_for_planning_click_failure_propagated():
    """Child reports the click failed (element not found) — surface gracefully."""
    sess = _make_form_open_session({"ok": False, "failure_reason": "unknown_eid"})
    generic_driver._sessions["sess_fail_click"] = sess

    result = webui_open_form_for_planning(
        "sess_fail_click", {"role": "button", "name": "Add", "action": "click"}
    )

    assert result["error"] == "open_form_click_failed"
    assert result["failure_reason"] == "unknown_eid"


def test_open_form_for_planning_subprocess_error():
    """Subprocess crash on send closes the session and returns an error."""
    sess = MagicMock()
    sess.is_alive.return_value = True
    sess.send.side_effect = SubprocessFlowError(
        flow="webui_session",
        error="pipe broken",
        exc_type="BrokenPipeError",
        stderr="",
    )
    generic_driver._sessions["sess_crash"] = sess

    result = webui_open_form_for_planning(
        "sess_crash", {"role": "button", "name": "Add", "action": "click"}
    )

    assert result["error"] == "open_form_subprocess_error"
    assert result["exc_type"] == "BrokenPipeError"
    # Session removed after crash so the next caller rebuilds.
    assert "sess_crash" not in generic_driver._sessions
