"""Child-loop tests for `_do_act` — the self-heal state machine.

These tests exercise `_do_act` directly (not through stdin/stdout) so we
can assert real Python flow control: did `locator.click()` get called
exactly once on a TimeoutError? Did `locator.fill()` retry exactly
once after the first failure?

The CLAUDE.md §4 "never auto-retry on writes" rule is enforced here. A
parent-side test that only checks the returned `failure_reason` string
could pass against a buggy implementation that hard-codes the string;
asserting `mock_locator.click.call_count == 1` catches that.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from backend.webui_agent._playwright_subprocess import _do_act

pytestmark = pytest.mark.webui


def _make_locator_for_act(*, action: str = "click") -> MagicMock:
    """Return a MagicMock locator usable by `_do_act`.

    Provides the methods _do_act might invoke + the post-failure probes
    (is_visible / is_enabled) defaulting to "everything looks fine".
    """
    loc = MagicMock()
    loc.is_visible.return_value = True
    loc.is_enabled.return_value = True
    return loc


def _patched_describe_page(view_id: str = "post", locator_map: dict | None = None):
    """Context manager that stubs describe_page to return a known view + map."""
    view = {
        "view_id": view_id,
        "url": "https://lab/",
        "title": "T",
        "elements": [],
        "modals": [],
        "errors": [],
    }
    return patch(
        "backend.webui_agent.semantic_dom.describe_page",
        return_value=(view, locator_map or {}),
    )


# ---------------------------------------------------------------------------
# THE crucial test: click on TimeoutError is NEVER retried.
# ---------------------------------------------------------------------------


def test_click_timeout_does_not_retry():
    """Click on TimeoutError must NEVER be retried (CLAUDE.md §4).

    Cisco WebUI Apply clicks fire via XHR; a TimeoutError after
    locator.click() may mean the network call already landed at the
    router. Retrying = duplicate router write.
    """
    loc = _make_locator_for_act()
    loc.click.side_effect = PlaywrightTimeoutError("element click intercepted by overlay")

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    with _patched_describe_page(locator_map={"e_001": loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v_pre",
            msg={
                "view_id": "v_pre",
                "eid": "e_001",
                "action": "click",
                "value": None,
            },
            ev=ev,
        )

    # THE CRUCIAL ASSERTIONS — both must hold or CLAUDE.md §4 is broken.
    assert loc.click.call_count == 1, (
        "click was retried on TimeoutError — this is a router-write-duplication risk"
    )
    assert reply["ok"] is False
    assert reply["failure_reason"] == "click_timeout_unsafe_retry"
    # attempts reports the index of the failing attempt (0 = first try).
    assert reply["attempts"] == 0


# ---------------------------------------------------------------------------
# Non-click TimeoutError DOES retry once.
# ---------------------------------------------------------------------------


def test_fill_timeout_retries_once_then_succeeds():
    """fill on TimeoutError retries once after re-describe + healthy probe."""
    loc = _make_locator_for_act()
    # First call raises, second succeeds.
    loc.fill.side_effect = [PlaywrightTimeoutError("timeout"), None]

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    with _patched_describe_page(locator_map={"e_001": loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v",
            msg={
                "view_id": "v",
                "eid": "e_001",
                "action": "fill",
                "value": "LAB-R5",
            },
            ev=ev,
        )

    # Retried exactly once.
    assert loc.fill.call_count == 2
    assert reply["ok"] is True
    assert reply["attempts"] == 1  # idx 0 failed, idx 1 succeeded


def test_fill_timeout_with_missing_element_reports_element_missing():
    """If re-describe shows the eid is gone, surface element_missing, no retry."""
    loc = _make_locator_for_act()
    loc.fill.side_effect = PlaywrightTimeoutError("timeout")

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    # Re-describe returns an EMPTY locator_map — element is gone.
    with _patched_describe_page(locator_map={}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v",
            msg={
                "view_id": "v",
                "eid": "e_001",
                "action": "fill",
                "value": "X",
            },
            ev=ev,
        )

    assert loc.fill.call_count == 1  # No retry — element was gone after first fail.
    assert reply["ok"] is False
    assert reply["failure_reason"] == "element_missing"


def test_fill_timeout_with_disabled_element_reports_element_disabled():
    loc = _make_locator_for_act()
    loc.fill.side_effect = PlaywrightTimeoutError("timeout")

    # Post-fail probe: element exists but is_enabled=False.
    refreshed_loc = MagicMock()
    refreshed_loc.is_visible.return_value = True
    refreshed_loc.is_enabled.return_value = False

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    with _patched_describe_page(locator_map={"e_001": refreshed_loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v",
            msg={
                "view_id": "v",
                "eid": "e_001",
                "action": "fill",
                "value": "X",
            },
            ev=ev,
        )

    assert reply["failure_reason"] == "element_disabled"


def test_fill_timeout_with_hidden_element_reports_element_hidden():
    loc = _make_locator_for_act()
    loc.fill.side_effect = PlaywrightTimeoutError("timeout")

    # Post-fail probe: is_visible=False.
    refreshed_loc = MagicMock()
    refreshed_loc.is_visible.return_value = False
    refreshed_loc.is_enabled.return_value = True

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    with _patched_describe_page(locator_map={"e_001": refreshed_loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v",
            msg={
                "view_id": "v",
                "eid": "e_001",
                "action": "fill",
                "value": "X",
            },
            ev=ev,
        )

    assert reply["failure_reason"] == "element_hidden"


# ---------------------------------------------------------------------------
# view_id staleness + eid lookup
# ---------------------------------------------------------------------------


def test_stale_view_id_short_circuits_without_acting():
    loc = _make_locator_for_act()
    page = MagicMock()
    ev = MagicMock()

    with _patched_describe_page(locator_map={"e_001": loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v_current",
            msg={
                "view_id": "v_stale",  # mismatch
                "eid": "e_001",
                "action": "click",
                "value": None,
            },
            ev=ev,
        )

    assert reply["ok"] is False
    assert reply["failure_reason"] == "stale_view"
    # No action attempted — the click side-effect was never even consulted.
    assert loc.click.call_count == 0


def test_unknown_eid_short_circuits_without_acting():
    loc = _make_locator_for_act()
    page = MagicMock()
    ev = MagicMock()

    with _patched_describe_page(locator_map={"e_001": loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v",
            msg={
                "view_id": "v",
                "eid": "e_999",  # not in map
                "action": "click",
                "value": None,
            },
            ev=ev,
        )

    assert reply["failure_reason"] == "unknown_eid"
    assert loc.click.call_count == 0


def test_unknown_action_short_circuits():
    loc = _make_locator_for_act()
    page = MagicMock()
    ev = MagicMock()

    with _patched_describe_page(locator_map={"e_001": loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v",
            msg={
                "view_id": "v",
                "eid": "e_001",
                "action": "type",  # not in _VALID_ACTIONS
                "value": "X",
            },
            ev=ev,
        )

    assert reply["failure_reason"] == "unknown_action"
