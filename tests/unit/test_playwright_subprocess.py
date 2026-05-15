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


# ---------------------------------------------------------------------------
# act_by_intent — intent resolver delegates to login.first_match
# ---------------------------------------------------------------------------


def test_act_by_intent_resolves_via_first_match():
    """Verify _do_act_by_intent delegates to `login.first_match` rather
    than reimplementing a fourth strategy walker.

    Builds a fake locator_map where one entry (e_007) has the same
    bounding box as what first_match returns; asserts the reverse-lookup
    selects e_007 and dispatches the click through it.
    """
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    # `chosen_loc` is what first_match returns; bbox = (100, 200, 80, 30).
    chosen_loc = _make_locator_for_act()
    chosen_loc.bounding_box.return_value = {
        "x": 100.0,
        "y": 200.0,
        "width": 80.0,
        "height": 30.0,
    }

    # `matching_loc` shares the same bbox — reverse-lookup must pick this one.
    matching_loc = _make_locator_for_act()
    matching_loc.bounding_box.return_value = {
        "x": 100.0,
        "y": 200.0,
        "width": 80.0,
        "height": 30.0,
    }

    # `other_loc` has a different bbox — should be skipped.
    other_loc = _make_locator_for_act()
    other_loc.bounding_box.return_value = {
        "x": 500.0,
        "y": 500.0,
        "width": 60.0,
        "height": 30.0,
    }

    fresh_map = {"e_001": other_loc, "e_007": matching_loc}
    fresh_view = {
        "view_id": "fresh",
        "url": "https://lab/",
        "title": "T",
        "elements": [],
        "modals": [],
        "errors": [],
    }

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    with (
        patch("backend.webui_agent.login.first_match", return_value=chosen_loc) as mock_first_match,
        patch(
            "backend.webui_agent.semantic_dom.describe_page",
            return_value=(fresh_view, fresh_map),
        ),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any_old",
            msg={
                "intent": {
                    "role": "button",
                    "name": "Apply",
                    "action": "click",
                    "value": None,
                },
            },
            ev=ev,
        )

    # first_match was called with the strategy list.
    mock_first_match.assert_called_once()
    strategies = mock_first_match.call_args.args[1]
    assert strategies[0] == {"role": "button", "name": "Apply"}
    assert strategies[1] == {"label": "Apply"}
    assert strategies[2] == {"text": "Apply"}

    # Reverse-lookup landed on e_007 (matching bbox), and the click was
    # dispatched through matching_loc — NOT chosen_loc or other_loc.
    assert reply["chosen_eid"] == "e_007"
    assert matching_loc.click.call_count == 1
    assert chosen_loc.click.call_count == 0
    assert other_loc.click.call_count == 0
    assert reply["ok"] is True


def test_act_by_intent_returns_unknown_eid_when_first_match_returns_none():
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    with (
        patch("backend.webui_agent.login.first_match", return_value=None),
        _patched_describe_page(),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg={
                "intent": {
                    "role": "button",
                    "name": "DoesNotExist",
                    "action": "click",
                },
            },
            ev=ev,
        )

    assert reply["ok"] is False
    assert reply["failure_reason"] == "unknown_eid"
    assert reply["chosen_eid"] is None


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


# ---------------------------------------------------------------------------
# QW3 — sensitive-text deny-list in _do_act_by_intent
# ---------------------------------------------------------------------------


def test_act_by_intent_denies_sensitive_text():
    """_do_act_by_intent must refuse to act on locators whose accessible name
    matches a phrase in _SENSITIVE_DENY_LIST, without clicking/filling at all.
    """
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    page = MagicMock()
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    chosen_loc = MagicMock()
    # Mixed case to verify case-insensitive match
    chosen_loc.get_attribute.return_value = "Factory Reset"

    post_view = {
        "view_id": "post",
        "url": "",
        "title": "",
        "elements": [],
        "modals": [],
        "errors": [],
    }

    with (
        patch("backend.webui_agent.login.first_match", return_value=chosen_loc),
        patch(
            "backend.webui_agent.semantic_dom.describe_page",
            return_value=(post_view, {}),
        ),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id=None,
            msg={
                "intent": {
                    "role": "button",
                    "name": "factory reset",
                    "action": "click",
                    "value": None,
                }
            },
            ev=ev,
        )

    assert reply["ok"] is False
    assert reply["failure_reason"] == "sensitive_text_denied"
    assert reply["denied_phrase"] == "factory reset"
    # THE CRUCIAL ASSERTIONS — action must never have executed
    assert chosen_loc.click.call_count == 0
    assert chosen_loc.fill.call_count == 0


# ---------------------------------------------------------------------------
# QW4 — URL-origin guard in _resolve_target_url
# ---------------------------------------------------------------------------


def test_resolve_target_url_rejects_foreign_origin():
    """_resolve_target_url must raise RuntimeError for absolute URLs whose
    hostname does not match the configured router_host.
    """
    from unittest.mock import patch as _patch

    from backend.webui_agent._playwright_subprocess import _resolve_target_url

    mock_settings = MagicMock()
    mock_settings.router_host = "192.168.10.1"
    mock_settings.router_webui_base_url = "https://192.168.10.1/"

    page = MagicMock()
    page.url = "https://192.168.10.1/webui/#/general"

    with _patch("backend.core.settings.get_settings", return_value=mock_settings):
        # Foreign origin must raise.
        with pytest.raises(RuntimeError, match="refused"):
            _resolve_target_url(page, "https://evil.example/foo")

        # Matching origin must pass through unchanged.
        result = _resolve_target_url(page, "https://192.168.10.1/foo")
        assert result == "https://192.168.10.1/foo"

        # Relative path must still work without raising.
        result = _resolve_target_url(page, "/general")
        assert "evil.example" not in result
        assert not result.startswith("http://evil")
        assert not result.startswith("https://evil")


# ---------------------------------------------------------------------------
# _eid_for_intent — Phase 3.4 spatial-label name collision fix
# ---------------------------------------------------------------------------


def _view_with(*elements: dict, modals: list[dict] | None = None) -> dict:
    return {
        "view_id": "test",
        "url": "https://x",
        "title": "t",
        "elements": list(elements),
        "modals": modals or [],
        "errors": [],
    }


def test_eid_for_intent_prefers_textbox_over_link_for_prefix_name():
    """Static-route page failure mode: page has BOTH a `textbox name='Prefix'`
    (form input) and a `link name='Prefix'` (column header). When the
    planner asks for ``{role: textbox}``, return the textbox, not the link.
    """
    from backend.webui_agent._playwright_subprocess import _eid_for_intent

    view = _view_with(
        {"eid": "e_018", "role": "link", "name": "Prefix", "enabled": True},
        {
            "eid": "e_003",
            "role": "textbox",
            "name": "Prefix",
            "enabled": True,
            "required": True,
        },
    )
    assert _eid_for_intent(view, "textbox", "Prefix") == "e_003"
    assert _eid_for_intent(view, "link", "Prefix") == "e_018"


def test_eid_for_intent_tie_breaks_on_required_then_enabled():
    """Two textboxes with same name → prefer required=True; if none required,
    prefer enabled=True; otherwise return first."""
    from backend.webui_agent._playwright_subprocess import _eid_for_intent

    view = _view_with(
        {"eid": "e_001", "role": "textbox", "name": "Prefix", "enabled": True},
        {
            "eid": "e_002",
            "role": "textbox",
            "name": "Prefix",
            "enabled": True,
            "required": True,
        },
    )
    assert _eid_for_intent(view, "textbox", "Prefix") == "e_002"  # required wins

    view2 = _view_with(
        {"eid": "e_001", "role": "textbox", "name": "X", "enabled": False},
        {"eid": "e_002", "role": "textbox", "name": "X", "enabled": True},
    )
    assert _eid_for_intent(view2, "textbox", "X") == "e_002"  # enabled wins

    view3 = _view_with(
        {"eid": "e_001", "role": "textbox", "name": "Y"},
        {"eid": "e_002", "role": "textbox", "name": "Y"},
    )
    assert _eid_for_intent(view3, "textbox", "Y") == "e_001"  # first hit


def test_eid_for_intent_returns_none_when_no_match():
    from backend.webui_agent._playwright_subprocess import _eid_for_intent

    view = _view_with(
        {"eid": "e_001", "role": "button", "name": "Apply"},
    )
    assert _eid_for_intent(view, "textbox", "Prefix") is None
    assert _eid_for_intent(view, "button", "Cancel") is None


def test_eid_for_intent_searches_modals_too():
    """Form elements inside a modal must be findable via the same path."""
    from backend.webui_agent._playwright_subprocess import _eid_for_intent

    view = _view_with(
        {"eid": "e_001", "role": "button", "name": "Add"},
        modals=[{"eid": "m_001", "role": "textbox", "name": "Prefix", "required": True}],
    )
    assert _eid_for_intent(view, "textbox", "Prefix") == "m_001"


def test_eid_for_intent_rejects_non_string_inputs():
    """Defensive: bad inputs return None instead of raising."""
    from backend.webui_agent._playwright_subprocess import _eid_for_intent

    view = _view_with({"eid": "e_001", "role": "textbox", "name": "Prefix"})
    assert _eid_for_intent(view, None, "Prefix") is None  # type: ignore[arg-type]
    assert _eid_for_intent(view, "textbox", None) is None  # type: ignore[arg-type]
    assert _eid_for_intent(view, 123, "Prefix") is None  # type: ignore[arg-type]
