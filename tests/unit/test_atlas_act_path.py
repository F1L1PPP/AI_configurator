"""Unit tests for C3: Atlas act-path session ops + generic_driver wrappers.

Subprocess-level tests call `_do_act_by_field` / `_do_apply_control` directly
with MagicMock page/ev and a real RouteAtlas (no Playwright child needed).

Generic-driver-level tests inject a fake session into ``generic_driver._sessions``
and assert the wrapper's HITL/error/success behaviour (mirrors test_generic_driver.py).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from backend.webui_agent import generic_driver
from backend.webui_agent._playwright_subprocess import (
    _do_act_by_field,
    _do_apply_control,
)
from backend.webui_agent._subprocess import SubprocessFlowError
from backend.webui_agent.atlas.adapters import LocatorResolutionError
from backend.webui_agent.atlas.schema import (
    ControlSpec,
    FieldSpec,
    LocatorSpec,
    RouteAtlas,
)
from backend.webui_agent.generic_driver import (
    webui_act_field,
    webui_apply_control,
    webui_perceive,
    webui_verify_a11y,
)

pytestmark = pytest.mark.webui


# ---------------------------------------------------------------------------
# Shared atlas fixture
# ---------------------------------------------------------------------------


def _make_atlas(*, field_key: str = "hostname", label: str = "Hostname") -> RouteAtlas:
    """Build a minimal RouteAtlas with one field and one apply_control."""
    locspec = LocatorSpec(strategy="name", value="hostname")
    field = FieldSpec(
        key=field_key,
        label=label,
        role="textbox",
        widget="input",
        locator=locspec,
    )
    apply_locspec = LocatorSpec(strategy="get_by_role", role="button", name="Apply to Device")
    apply_ctrl = ControlSpec(
        key="apply",
        label="Apply to Device",
        role="button",
        locator=apply_locspec,
        is_router_write=True,
    )
    return RouteAtlas(
        route="#/general",
        device_fingerprint="c1111-4p__17-6-3a",
        fields=[field],
        apply_controls=[apply_ctrl],
    )


def _make_ev() -> MagicMock:
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    return ev


def _make_page() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Autouse fixture: clear generic_driver module state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sessions():
    generic_driver._sessions.clear()
    generic_driver._pre_snapshotted.clear()
    generic_driver._vision_eid_failures.clear()
    yield
    generic_driver._sessions.clear()
    generic_driver._pre_snapshotted.clear()
    generic_driver._vision_eid_failures.clear()


# ===========================================================================
# _do_act_by_field — subprocess-level tests
# ===========================================================================


_SENTINEL = object()  # sentinel for "use default atlas"


class TestDoActByField:
    def _call(self, msg: dict, atlas=_SENTINEL, fake_adapter=None):
        """Helper: patch get_adapter and call _do_act_by_field."""
        if atlas is _SENTINEL:
            atlas = _make_atlas()
        page = _make_page()
        ev = _make_ev()
        if fake_adapter is not None:
            with patch(
                "backend.webui_agent._playwright_subprocess.get_adapter",
                return_value=fake_adapter,
            ):
                return _do_act_by_field(page, atlas, msg, ev)
        return _do_act_by_field(page, atlas, msg, ev)

    # --- happy path ---

    def test_success_with_read_back_match(self):
        """apply succeeds + read_back matches → ok True."""
        adapter = MagicMock()
        adapter.read_back.return_value = "LAB-R1"
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is True
        assert result["field_key"] == "hostname"
        assert result["attempts"] == 0

    def test_success_read_back_none_skips_verify(self):
        """read_back returns None → no mismatch check, ok True."""
        adapter = MagicMock()
        adapter.read_back.return_value = None
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is True

    def test_success_string_case_insensitive_match(self):
        """String compare is case-insensitive and trimmed."""
        adapter = MagicMock()
        adapter.read_back.return_value = "  lab-r1  "  # whitespace + different case
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is True

    # --- read-back mismatch ---

    def test_verify_mismatch(self):
        """apply succeeds but read_back disagrees → verify_mismatch, no retry."""
        adapter = MagicMock()
        adapter.read_back.return_value = "WRONG-VALUE"
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "verify_mismatch"
        assert result["expected"] == "LAB-R1"
        assert result["got"] == "WRONG-VALUE"
        # apply called only once — no retry on mismatch.
        adapter.apply.assert_called_once()

    def test_combobox_read_back_mismatch_is_advisory(self):
        """Kendo combobox read-back is the backing <select> VALUE attribute,
        which can differ from the chosen display text — a mismatch must NOT
        fail (deep-audit fix; the CLI verify is the real backstop). Text-input
        mismatch still fails (see test_verify_mismatch)."""
        combo_field = FieldSpec(
            key="subnet_mask",
            label="Subnet Mask",
            role="combobox",
            widget="kendo_combobox",
            options=["255.255.255.0"],
            kendo_select_name="subnetMask",
            locator=LocatorSpec(strategy="name", value="subnetMask"),
        )
        atlas = RouteAtlas(route="#/dhcp", device_fingerprint="fp", fields=[combo_field])
        adapter = MagicMock()
        adapter.read_back.return_value = "24"  # value attr != display text
        result = self._call(
            {"field_key": "subnet_mask", "value": "255.255.255.0"},
            atlas=atlas,
            fake_adapter=adapter,
        )
        assert result["ok"] is True

    def test_idempotent_skip_when_already_set(self):
        """If read_back already equals the target, act_field is a no-op success —
        no adapter.apply. The robust path for Kendo dropdowns already at the
        requested value (Subnet Mask already 255.255.255.0 for a /24), which
        otherwise triggers a fragile open/click that times out."""
        adapter = MagicMock()
        adapter.read_back.return_value = "255.255.255.0"  # already set
        result = self._call(
            {"field_key": "hostname", "value": "255.255.255.0"},
            fake_adapter=adapter,
        )
        assert result["ok"] is True
        assert result.get("skipped") == "already_set"
        adapter.apply.assert_not_called()  # idempotent — no action taken

    def test_bool_verify_mismatch(self):
        """Bool widget: expected True but got False → verify_mismatch."""
        adapter = MagicMock()
        adapter.read_back.return_value = False  # bool
        result = self._call(
            {"field_key": "hostname", "value": "true"},  # truthy string
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "verify_mismatch"

    def test_bool_verify_match(self):
        """Bool widget: expected True and got True → ok."""
        adapter = MagicMock()
        adapter.read_back.return_value = True
        result = self._call(
            {"field_key": "hostname", "value": "true"},
            fake_adapter=adapter,
        )
        assert result["ok"] is True

    # --- PlaywrightTimeoutError: retry once ---

    def test_timeout_retries_once_then_succeeds(self):
        """PlaywrightTimeoutError on first attempt → retried; second succeeds."""
        adapter = MagicMock()
        adapter.apply.side_effect = [PlaywrightTimeoutError("timeout"), None]
        adapter.read_back.return_value = None
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is True
        assert result["attempts"] == 1  # attempt_idx=1 when retry succeeded
        assert adapter.apply.call_count == 2

    def test_timeout_twice_gives_element_intercepted(self):
        """PlaywrightTimeoutError on both attempts → element_intercepted."""
        adapter = MagicMock()
        adapter.apply.side_effect = [
            PlaywrightTimeoutError("timeout"),
            PlaywrightTimeoutError("timeout again"),
        ]
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "element_intercepted"
        assert adapter.apply.call_count == 2

    # --- ValueError: dead-end, no retry ---

    def test_value_rejected_not_retried(self):
        """ValueError → value_rejected and apply called ONCE."""
        adapter = MagicMock()
        adapter.apply.side_effect = ValueError("value not in options")
        result = self._call(
            {"field_key": "hostname", "value": "BOGUS"},
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "value_rejected"
        assert "value not in options" in result["error"]
        adapter.apply.assert_called_once()  # NEVER retried

    # --- LocatorResolutionError: no retry ---

    def test_locator_resolution_error_unmapped_field(self):
        """LocatorResolutionError → unmapped_field, apply called ONCE."""
        adapter = MagicMock()
        adapter.apply.side_effect = LocatorResolutionError("hostname")
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "unmapped_field"
        adapter.apply.assert_called_once()

    # --- deny-list ---

    def test_deny_list_field_key(self):
        """field_key containing a deny phrase → sensitive_denied."""
        atlas = _make_atlas(field_key="factory reset", label="Factory Reset")
        adapter = MagicMock()
        result = self._call(
            {"field_key": "factory reset", "value": "anything"},
            atlas=atlas,
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "sensitive_denied"
        adapter.apply.assert_not_called()

    def test_deny_list_value(self):
        """value containing a deny phrase → sensitive_denied."""
        adapter = MagicMock()
        result = self._call(
            {"field_key": "hostname", "value": "factory reset me"},
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "sensitive_denied"
        adapter.apply.assert_not_called()

    def test_deny_list_label(self):
        """field label containing a deny phrase → sensitive_denied."""
        atlas = _make_atlas(field_key="dangerous_field", label="Reboot Device Now")
        adapter = MagicMock()
        result = self._call(
            {"field_key": "dangerous_field", "value": "yes"},
            atlas=atlas,
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "sensitive_denied"

    # --- unknown field_key ---

    def test_unknown_field_key(self):
        """field_key not in atlas → unknown_field_key."""
        result = self._call({"field_key": "nonexistent_field", "value": "x"})
        assert result["ok"] is False
        assert result["failure_reason"] == "unknown_field_key"
        assert result["field_key"] == "nonexistent_field"

    # --- unknown_error ---

    def test_unknown_error_surfaces_exc_type(self):
        """Generic exception → unknown_error with exc_type."""
        adapter = MagicMock()
        adapter.apply.side_effect = RuntimeError("boom")
        result = self._call(
            {"field_key": "hostname", "value": "LAB-R1"},
            fake_adapter=adapter,
        )
        assert result["ok"] is False
        assert result["failure_reason"] == "unknown_error"
        assert result["exc_type"] == "RuntimeError"
        assert "boom" in result["error"]


# ===========================================================================
# _do_apply_control — subprocess-level tests
# ===========================================================================


class TestDoApplyControl:
    def _call(self, atlas=_SENTINEL, msg=None, fake_locate=None):
        page = _make_page()
        ev = _make_ev()
        if atlas is _SENTINEL:
            atlas = _make_atlas()
        if msg is None:
            msg = {}

        if fake_locate is not None:
            with patch(
                "backend.webui_agent._playwright_subprocess._locate_control",
                return_value=fake_locate,
            ):
                return _do_apply_control(page, atlas, msg, ev)
        return _do_apply_control(page, atlas, msg, ev)

    def test_success(self):
        """Normal path: locate resolves + click succeeds → ok True."""
        loc = MagicMock()
        loc.click.return_value = None
        result = self._call(fake_locate=loc)
        assert result["ok"] is True
        loc.click.assert_called_once()

    def test_click_timeout_not_retried(self):
        """PlaywrightTimeoutError → click_timeout_unsafe_retry, click called ONCE."""
        loc = MagicMock()
        loc.click.side_effect = PlaywrightTimeoutError("timeout")
        result = self._call(fake_locate=loc)
        assert result["ok"] is False
        assert result["failure_reason"] == "click_timeout_unsafe_retry"
        loc.click.assert_called_once()  # NEVER retried — CLAUDE.md §4

    def test_cancel_named_control_refused(self):
        """An apply_control whose label resolves to Cancel → apply_resolved_to_cancel."""
        cancel_locspec = LocatorSpec(strategy="get_by_role", role="button", name="Cancel")
        cancel_ctrl = ControlSpec(
            key="cancel_btn",
            label="Cancel",
            role="button",
            locator=cancel_locspec,
            is_router_write=False,
        )
        atlas = RouteAtlas(
            route="#/general",
            device_fingerprint="c1111-4p__17-6-3a",
            apply_controls=[cancel_ctrl],
        )
        result = self._call(atlas=atlas)
        assert result["ok"] is False
        assert result["failure_reason"] == "apply_resolved_to_cancel"

    def test_no_apply_controls_returns_no_apply_control(self):
        """Atlas with no apply_controls → no_apply_control."""
        atlas = RouteAtlas(
            route="#/general",
            device_fingerprint="c1111-4p__17-6-3a",
        )
        result = self._call(atlas=atlas)
        assert result["ok"] is False
        assert result["failure_reason"] == "no_apply_control"

    def test_no_atlas_returns_no_apply_control(self):
        """None atlas → no_apply_control."""
        result = _do_apply_control(_make_page(), None, {}, _make_ev())
        assert result["ok"] is False
        assert result["failure_reason"] == "no_apply_control"

    def test_locator_resolution_error_unmapped_field(self):
        """_locate_control raises LocatorResolutionError → unmapped_field."""
        with patch(
            "backend.webui_agent._playwright_subprocess._locate_control",
            side_effect=LocatorResolutionError("apply"),
        ):
            result = _do_apply_control(_make_page(), _make_atlas(), {}, _make_ev())
        assert result["ok"] is False
        assert result["failure_reason"] == "unmapped_field"

    def test_select_by_key(self):
        """msg with key= picks the matching control."""
        loc1 = MagicMock()
        loc1.click.return_value = None
        loc2 = MagicMock()
        loc2.click.return_value = None

        locspec1 = LocatorSpec(strategy="name", value="save_btn")
        locspec2 = LocatorSpec(strategy="name", value="apply_btn")
        ctrl1 = ControlSpec(key="save", label="Save", role="button", locator=locspec1)
        ctrl2 = ControlSpec(
            key="apply",
            label="Apply to Device",
            role="button",
            locator=locspec2,
            is_router_write=True,
        )
        atlas = RouteAtlas(
            route="#/general",
            device_fingerprint="c1111-4p__17-6-3a",
            apply_controls=[ctrl1, ctrl2],
        )

        # Ask for ctrl1 by key — _locate_control returns loc1.
        with patch(
            "backend.webui_agent._playwright_subprocess._locate_control",
            return_value=loc1,
        ) as mock_locate:
            result = _do_apply_control(_make_page(), atlas, {"key": "save"}, _make_ev())

        assert result["ok"] is True
        # The control passed to _locate_control must be ctrl1.
        actual_control = mock_locate.call_args[0][1]
        assert actual_control.key == "save"


# ===========================================================================
# verify_a11y text scan (via _playwright_subprocess op handler logic — tested
# indirectly through the helper function logic)
# ===========================================================================


class TestVerifyA11y:
    """Test the verify_a11y text-scan logic via the flatten_interactive path."""

    def test_present(self):
        """If the accessibility tree contains the target text, present=True."""
        from backend.webui_agent.atlas.reconcile import flatten_interactive

        snap = {
            "role": "WebArea",
            "name": "",
            "children": [
                {"role": "textbox", "name": "Hostname", "value": "LAB-R1"},
                {"role": "button", "name": "Apply to Device", "value": ""},
            ],
        }
        nodes = flatten_interactive(snap)
        contains = "LAB-R1"
        present = any(
            contains.lower() in (str(n.get("name", "")) + " " + str(n.get("value", ""))).lower()
            for n in nodes
        )
        assert present is True

    def test_absent(self):
        """If the target text is not in the tree, present=False."""
        from backend.webui_agent.atlas.reconcile import flatten_interactive

        snap = {
            "role": "WebArea",
            "name": "",
            "children": [
                {"role": "textbox", "name": "Hostname", "value": ""},
            ],
        }
        nodes = flatten_interactive(snap)
        contains = "NOTHERE"
        present = any(
            contains.lower() in (str(n.get("name", "")) + " " + str(n.get("value", ""))).lower()
            for n in nodes
        )
        assert present is False


# ===========================================================================
# generic_driver wrappers — driver-level tests
# ===========================================================================


def _make_fake_session(send_reply: dict | None = None) -> MagicMock:
    sess = MagicMock()
    sess.is_alive.return_value = True
    if send_reply is not None:
        sess.send.return_value = send_reply
    return sess


class TestWebuiPerceve:
    def test_returns_view_no_approval_needed(self):
        """webui_perceive is read-only — no is_approved gate."""
        fake_sess = _make_fake_session(
            {
                "ok": True,
                "view": {"fields": []},
                "drift": False,
                "captured": False,
                "missing_required": [],
                "unmapped_fields": [],
            }
        )
        generic_driver._sessions["sess_P1"] = fake_sess

        result = webui_perceive("sess_P1", route="#/general", device_fingerprint="c1111__17-6")

        fake_sess.send.assert_called_once_with(
            {"op": "perceive", "route": "#/general", "device_fingerprint": "c1111__17-6"}
        )
        assert result["session_id"] == "sess_P1"
        assert result["drift"] is False
        assert result["captured"] is False

    def test_session_not_found(self):
        result = webui_perceive("nonexistent_sess")
        assert result["error"] == "session_not_found"

    def test_subprocess_error_closes_session(self):
        fake_sess = _make_fake_session()
        fake_sess.send.side_effect = SubprocessFlowError(
            flow="perceive", error="crashed", exc_type="RuntimeError", stderr=""
        )
        generic_driver._sessions["sess_P2"] = fake_sess

        result = webui_perceive("sess_P2")

        assert result["error"] == "webui_perceive_failed"
        assert "sess_P2" not in generic_driver._sessions

    def test_not_ok_reply_surfaces_error(self):
        fake_sess = _make_fake_session(
            {"ok": False, "error": "atlas capture failed", "exc_type": "RuntimeError"}
        )
        generic_driver._sessions["sess_P3"] = fake_sess

        result = webui_perceive("sess_P3")
        assert result["error"] == "webui_perceive_failed"


class TestWebuiActField:
    def test_not_approved_returns_error(self):
        with patch("backend.webui_agent.generic_driver.is_approved", return_value=False):
            result = webui_act_field("sess_AF1", "hostname", "LAB-R1", "act_001")
        assert result["error"] == "not_approved"

    def test_approved_success(self):
        fake_sess = _make_fake_session({"ok": True, "field_key": "hostname", "attempts": 0})
        generic_driver._sessions["sess_AF2"] = fake_sess

        with (
            patch("backend.webui_agent.generic_driver.is_approved", return_value=True),
            patch("backend.webui_agent.generic_driver.pool"),
            patch("backend.webui_agent.generic_driver.get_settings") as mock_settings,
        ):
            mock_settings.return_value.router_host = "192.168.1.1"
            mock_settings.return_value.router_ssh_user = "cisco"
            result = webui_act_field("sess_AF2", "hostname", "LAB-R1", "act_AF2")

        assert result["ok"] is True
        assert result["field_key"] == "hostname"
        assert result["session_id"] == "sess_AF2"

    def test_approved_soft_failure_does_not_mark_failed(self):
        """Soft failure (verify_mismatch etc.) must NOT call mark_failed."""
        fake_sess = _make_fake_session(
            {
                "ok": False,
                "failure_reason": "verify_mismatch",
                "field_key": "hostname",
            }
        )
        generic_driver._sessions["sess_AF3"] = fake_sess

        with (
            patch("backend.webui_agent.generic_driver.is_approved", return_value=True),
            patch("backend.webui_agent.generic_driver.mark_failed") as mock_mf,
        ):
            result = webui_act_field("sess_AF3", "hostname", "WRONG", "act_AF3")

        assert result["ok"] is False
        assert result["failure_reason"] == "verify_mismatch"
        mock_mf.assert_not_called()

    def test_subprocess_error_calls_mark_failed(self):
        fake_sess = _make_fake_session()
        fake_sess.send.side_effect = SubprocessFlowError(
            flow="act_field", error="crash", exc_type="RuntimeError", stderr=""
        )
        generic_driver._sessions["sess_AF4"] = fake_sess

        with (
            patch("backend.webui_agent.generic_driver.is_approved", return_value=True),
            patch("backend.webui_agent.generic_driver.mark_failed") as mock_mf,
        ):
            result = webui_act_field("sess_AF4", "hostname", "X", "act_AF4")

        assert result["error"] == "webui_act_field_failed"
        mock_mf.assert_called_once_with("act_AF4")
        assert "sess_AF4" not in generic_driver._sessions


class TestWebuiApplyControl:
    def test_not_approved_returns_error(self):
        with patch("backend.webui_agent.generic_driver.is_approved", return_value=False):
            result = webui_apply_control("sess_AC1", "act_001")
        assert result["error"] == "not_approved"

    def test_approved_success(self):
        fake_sess = _make_fake_session({"ok": True})
        generic_driver._sessions["sess_AC2"] = fake_sess

        with (
            patch("backend.webui_agent.generic_driver.is_approved", return_value=True),
            patch("backend.webui_agent.generic_driver.pool"),
            patch("backend.webui_agent.generic_driver.get_settings") as mock_settings,
        ):
            mock_settings.return_value.router_host = "192.168.1.1"
            mock_settings.return_value.router_ssh_user = "cisco"
            result = webui_apply_control("sess_AC2", "act_AC2")

        assert result["ok"] is True
        assert result["session_id"] == "sess_AC2"

    def test_soft_failure_does_not_mark_failed(self):
        fake_sess = _make_fake_session(
            {"ok": False, "failure_reason": "click_timeout_unsafe_retry"}
        )
        generic_driver._sessions["sess_AC3"] = fake_sess

        with (
            patch("backend.webui_agent.generic_driver.is_approved", return_value=True),
            patch("backend.webui_agent.generic_driver.mark_failed") as mock_mf,
        ):
            result = webui_apply_control("sess_AC3", "act_AC3")

        assert result["ok"] is False
        assert result["failure_reason"] == "click_timeout_unsafe_retry"
        mock_mf.assert_not_called()

    def test_subprocess_error_calls_mark_failed(self):
        fake_sess = _make_fake_session()
        fake_sess.send.side_effect = SubprocessFlowError(
            flow="apply_control", error="crash", exc_type="RuntimeError", stderr=""
        )
        generic_driver._sessions["sess_AC4"] = fake_sess

        with (
            patch("backend.webui_agent.generic_driver.is_approved", return_value=True),
            patch("backend.webui_agent.generic_driver.mark_failed") as mock_mf,
        ):
            result = webui_apply_control("sess_AC4", "act_AC4")

        assert result["error"] == "webui_apply_control_failed"
        mock_mf.assert_called_once_with("act_AC4")
        assert "sess_AC4" not in generic_driver._sessions


class TestWebuiVerifyA11y:
    def test_present_true(self):
        fake_sess = _make_fake_session({"ok": True, "present": True})
        generic_driver._sessions["sess_VA1"] = fake_sess

        result = webui_verify_a11y("sess_VA1", "DHCP pool created")

        fake_sess.send.assert_called_once_with(
            {"op": "verify_a11y", "contains": "DHCP pool created"}
        )
        assert result["present"] is True
        assert result["session_id"] == "sess_VA1"

    def test_present_false(self):
        fake_sess = _make_fake_session({"ok": True, "present": False})
        generic_driver._sessions["sess_VA2"] = fake_sess

        result = webui_verify_a11y("sess_VA2", "something absent")
        assert result["present"] is False

    def test_session_not_found(self):
        result = webui_verify_a11y("nonexistent", "anything")
        assert result["error"] == "session_not_found"

    def test_subprocess_error_closes_session(self):
        fake_sess = _make_fake_session()
        fake_sess.send.side_effect = SubprocessFlowError(
            flow="verify_a11y", error="crash", exc_type="RuntimeError", stderr=""
        )
        generic_driver._sessions["sess_VA3"] = fake_sess

        result = webui_verify_a11y("sess_VA3", "anything")

        assert result["error"] == "webui_verify_a11y_failed"
        assert "sess_VA3" not in generic_driver._sessions
