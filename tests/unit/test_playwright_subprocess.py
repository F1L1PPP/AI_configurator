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
    page.url = "http://router/webui/#/general"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = MagicMock()
    import tempfile
    from pathlib import Path as _Path

    settings_mock.selector_cache_path = _Path(tempfile.mkdtemp()) / "sc.json"

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", return_value=None),
        patch("backend.webui_agent.login.first_match", return_value=chosen_loc) as mock_first_match,
        patch(
            "backend.webui_agent.semantic_dom.describe_page",
            return_value=(fresh_view, fresh_map),
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
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
    ev.vision_call_count = 0

    with (
        patch("backend.webui_agent.login.first_match", return_value=None),
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", return_value=None),
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
    page.url = "http://router/webui/#/general"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    chosen_loc = MagicMock()
    # Mixed case to verify case-insensitive match
    chosen_loc.get_attribute.return_value = "Factory Reset"

    import tempfile
    from pathlib import Path as _Path

    settings_mock = MagicMock()
    settings_mock.selector_cache_path = _Path(tempfile.mkdtemp()) / "sc.json"

    post_view = {
        "view_id": "post",
        "url": "",
        "title": "",
        "elements": [],
        "modals": [],
        "errors": [],
    }

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", return_value=None),
        patch("backend.webui_agent.login.first_match", return_value=chosen_loc),
        patch(
            "backend.webui_agent.semantic_dom.describe_page",
            return_value=(post_view, {}),
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
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


# ---------------------------------------------------------------------------
# _settle_page — networkidle-with-fallback wait after each successful action
# ---------------------------------------------------------------------------


def test_settle_page_returns_when_networkidle_fires():
    """Healthy page reaches networkidle → no fallback sleep, fast return."""
    from backend.webui_agent._playwright_subprocess import _settle_page

    page = MagicMock()
    # wait_for_load_state returns normally (no exception) — simulates idle.
    page.wait_for_load_state.return_value = None

    with patch("time.sleep") as mock_sleep:
        _settle_page(page)

    page.wait_for_load_state.assert_called_once_with("networkidle", timeout=800)
    mock_sleep.assert_not_called()  # idle fired → no fallback


def test_settle_page_falls_back_to_sleep_on_networkidle_timeout():
    """Cisco pages with polling timers never reach networkidle → fallback to
    500ms sleep. Without this, modal-rendering races make describe_page
    snapshot a transient state (the ISIS Add bug)."""
    from backend.webui_agent._playwright_subprocess import _settle_page

    page = MagicMock()
    page.wait_for_load_state.side_effect = PlaywrightTimeoutError("never idle")

    with patch("time.sleep") as mock_sleep:
        _settle_page(page)

    page.wait_for_load_state.assert_called_once_with("networkidle", timeout=800)
    mock_sleep.assert_called_once_with(0.25)  # 250ms fallback


def test_settle_page_swallows_other_exceptions():
    """If wait_for_load_state raises something other than TimeoutError (page
    closed, navigation mid-flight), _settle_page must NOT raise — the
    describe_page that follows will surface a real error if there is one.
    """
    from backend.webui_agent._playwright_subprocess import _settle_page

    page = MagicMock()
    page.wait_for_load_state.side_effect = RuntimeError("page closed")

    # Should not raise
    _settle_page(page)


def test_do_act_calls_settle_on_success_path(monkeypatch):
    """Regression guard: every successful _do_act invocation must call
    _settle_page between the action and the post-action describe. Without
    this, the ISIS Add modal closes before describe captures it and the
    inner planner sees a blank view."""
    from backend.webui_agent import _playwright_subprocess as sub

    settle_calls: list[object] = []

    def fake_settle(page: object) -> None:
        settle_calls.append(page)

    monkeypatch.setattr(sub, "_settle_page", fake_settle)

    page = MagicMock()
    loc = _make_locator_for_act()
    locator_map = {"e_001": loc}
    ev = MagicMock()
    ev.session_dir = "/tmp/x"

    # describe_page is imported lazily inside _do_act — patch at the module
    # of origin (semantic_dom) to intercept the import-time lookup.
    with patch(
        "backend.webui_agent.semantic_dom.describe_page",
        return_value=(
            {
                "view_id": "post",
                "url": "x",
                "title": "t",
                "elements": [],
                "modals": [],
                "errors": [],
            },
            {},
        ),
    ):
        reply, _new_map, _new_vid = sub._do_act(
            page=page,
            locator_map=locator_map,
            current_view_id="pre",
            msg={"view_id": "pre", "eid": "e_001", "action": "click", "value": None},
            ev=ev,
        )

    assert reply["ok"] is True
    assert len(settle_calls) == 1, "must settle exactly once on the success path"
    assert settle_calls[0] is page


# ---------------------------------------------------------------------------
# _describe_with_retry tests
# ---------------------------------------------------------------------------

import backend.webui_agent._playwright_subprocess as _sub_mod  # noqa: E402


def _make_view(*, populated: bool, view_id: str = "v1") -> dict:
    """Return a minimal view dict with or without elements."""
    return {
        "view_id": view_id,
        "url": "https://lab/",
        "title": "T",
        "elements": [{"eid": "e_001", "role": "button", "name": "Apply"}] if populated else [],
        "modals": [],
        "errors": [],
    }


def test_describe_with_retry_returns_first_when_populated(monkeypatch):
    """When describe_page returns a populated view, no retry and no settle."""
    populated_view = _make_view(populated=True, view_id="v_first")
    locator_map = {"e_001": MagicMock()}

    describe_calls: list[int] = []
    settle_calls: list[int] = []

    def fake_describe(page):
        describe_calls.append(1)
        return populated_view, locator_map

    monkeypatch.setattr(_sub_mod, "_settle_page", lambda page: settle_calls.append(1))
    monkeypatch.setattr(
        "backend.webui_agent.semantic_dom.describe_page",
        fake_describe,
    )

    page = MagicMock()
    view, lmap = _sub_mod._describe_with_retry(page, max_attempts=2)

    assert view["view_id"] == "v_first"
    assert lmap is locator_map
    assert len(describe_calls) == 1, "describe_page must be called exactly once"
    assert len(settle_calls) == 0, "_settle_page must NOT be called when first view is populated"


def test_describe_with_retry_retries_when_view_empty(monkeypatch):
    """Empty first view triggers a settle + retry; returns the populated second view."""
    empty_view = _make_view(populated=False, view_id="v_empty")
    populated_view = _make_view(populated=True, view_id="v_populated")
    locator_map_pop = {"e_001": MagicMock()}

    call_sequence: list[str] = []
    describe_call_count: list[int] = [0]

    def fake_describe(page):
        describe_call_count[0] += 1
        call_sequence.append("describe")
        if describe_call_count[0] == 1:
            return empty_view, {}
        return populated_view, locator_map_pop

    def fake_settle(page):
        call_sequence.append("settle")

    monkeypatch.setattr(_sub_mod, "_settle_page", fake_settle)
    monkeypatch.setattr(
        "backend.webui_agent.semantic_dom.describe_page",
        fake_describe,
    )

    page = MagicMock()
    view, lmap = _sub_mod._describe_with_retry(page, max_attempts=2)

    assert view["view_id"] == "v_populated"
    assert lmap is locator_map_pop
    describe_count = call_sequence.count("describe")
    settle_count = call_sequence.count("settle")
    assert describe_count == 2, f"expected 2 describe calls, got {describe_count}"
    assert settle_count == 1, f"expected 1 settle call, got {settle_count}"
    # settle must happen between the two describes
    assert call_sequence == ["describe", "settle", "describe"]


def test_describe_with_retry_returns_empty_after_max_attempts(monkeypatch):
    """When all attempts return empty, return the last empty view — no exception."""
    empty_view = _make_view(populated=False, view_id="v_empty")

    describe_calls: list[int] = []
    settle_calls: list[int] = []

    def fake_describe(page):
        describe_calls.append(1)
        return empty_view, {}

    monkeypatch.setattr(_sub_mod, "_settle_page", lambda page: settle_calls.append(1))
    monkeypatch.setattr(
        "backend.webui_agent.semantic_dom.describe_page",
        fake_describe,
    )

    page = MagicMock()
    view, lmap = _sub_mod._describe_with_retry(page, max_attempts=2)

    assert view["view_id"] == "v_empty"
    assert lmap == {}
    assert len(describe_calls) == 2, "must attempt describe twice before giving up"
    assert len(settle_calls) == 1, "_settle_page must be called once between attempts"


# ---------------------------------------------------------------------------
# chunk 14g — vision-first selector resolution tests
# ---------------------------------------------------------------------------


def _make_settings_mock(tmp_path=None) -> MagicMock:
    """Return a settings mock with a valid selector_cache_path."""
    import tempfile
    from pathlib import Path as _Path

    settings = MagicMock()
    if tmp_path is None:
        tmp_path = _Path(tempfile.mkdtemp())
    settings.selector_cache_path = tmp_path / "selector_cache.json"
    return settings


def _base_intent_msg(role: str = "textbox", name: str = "Network", action: str = "fill") -> dict:
    return {"intent": {"role": role, "name": name, "action": action, "value": "10.0.0.0"}}


def _fresh_view_and_map() -> tuple[dict, dict]:
    view = {
        "view_id": "fresh",
        "url": "http://router/webui/#/dhcp",
        "title": "DHCP",
        "elements": [],
        "modals": [],
        "errors": [],
    }
    return view, {}


def test_act_by_intent_vision_fallback_used_when_eid_lookup_returns_none(tmp_path):
    """14h-F order: eid lookup finds nothing (describe has no match) → vision fires.

    When describe_page returns an empty view (no elements matching the intent),
    _eid_for_intent returns None and vision fallback is reached. first_match
    must NOT be called if vision provides a selector.
    """
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    selector = "input[aria-label='Network']"
    page = MagicMock()
    page.url = "http://router/webui/#/dhcp"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = _make_settings_mock(tmp_path)
    # Empty view: _eid_for_intent will return None, triggering vision fallback.
    fresh_view, fresh_map = _fresh_view_and_map()

    # _do_act needs the synthetic eid in the locator_map to succeed.
    # We patch _do_act directly so we can assert on the synthetic_eid arg.
    do_act_calls: list[dict] = []

    def fake_do_act(page_, locator_map_, view_id_, msg_, ev_):
        do_act_calls.append({"msg": msg_, "locator_map_keys": list(locator_map_.keys())})
        return ({"ok": True, "attempts": 0}, locator_map_, view_id_)

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", return_value=selector),
        patch("backend.webui_agent.login.first_match") as mock_first_match,
        patch(
            "backend.webui_agent.semantic_dom.describe_page", return_value=(fresh_view, fresh_map)
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
        patch("backend.webui_agent._playwright_subprocess._do_act", side_effect=fake_do_act),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg=_base_intent_msg(),
            ev=ev,
        )

    # first_match must NOT have been called — vision resolved before it.
    mock_first_match.assert_not_called()
    # _do_act was called and synthetic_eid starts with "vision_".
    assert len(do_act_calls) == 1
    assert do_act_calls[0]["msg"]["eid"].startswith("vision_")
    assert reply["resolved_via"] == "vision"


def test_act_by_intent_vision_path_enforces_sensitive_deny_list(tmp_path):
    """14g security regression guard.

    Vision-first inversion in 14g moved the primary resolution path to vision,
    bypassing the _SENSITIVE_DENY_LIST check that lived only in the heuristic
    path. Without the in-closure deny-list check added in this commit, a
    prompt-injected intent like {role: button, name: "Reboot"} would resolve
    via vision and click Reboot, bypassing the safeguard entirely. This test
    asserts the vision path now enforces the deny-list.
    """
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    selector = "button.danger-reboot"
    page = MagicMock()
    page.url = "http://router/webui/#/admin"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    # Locator's accessible name contains the deny-listed phrase "reboot".
    vision_loc = MagicMock()
    vision_loc.get_attribute.return_value = "Reboot Router"
    vision_loc.text_content.return_value = "Reboot Router"
    page.locator.return_value = vision_loc

    settings_mock = _make_settings_mock(tmp_path)
    fresh_view, fresh_map = _fresh_view_and_map()

    # _do_act must NOT be called — the deny-list short-circuits before it.
    do_act_called = False

    def fake_do_act(*args, **kwargs):
        nonlocal do_act_called
        do_act_called = True
        return ({"ok": True, "attempts": 0}, {}, "v")

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", return_value=selector),
        patch(
            "backend.webui_agent.semantic_dom.describe_page", return_value=(fresh_view, fresh_map)
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
        patch("backend.webui_agent._playwright_subprocess._do_act", side_effect=fake_do_act),
    ):
        # Intent asks for a button by name "Reboot" (the prompt-injection shape).
        msg = _base_intent_msg()
        msg["intent"] = {"role": "button", "name": "Reboot", "action": "click", "value": None}
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg=msg,
            ev=ev,
        )

    # The action must be denied — _do_act NOT called, sensitive_text_denied returned.
    assert do_act_called is False, "deny-list bypass: vision-resolved Reboot reached _do_act"
    assert reply["ok"] is False
    assert reply["failure_reason"] == "sensitive_text_denied"
    assert reply["resolved_via"] == "vision_denied"


def test_act_by_intent_falls_through_to_heuristics_when_vision_returns_none(tmp_path):
    """14h-F order: eid lookup None → vision None → first_match reached.

    With an empty describe view, _eid_for_intent returns None. vision also
    returns None. The code must fall through to first_match (Step 3).
    """
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    page = MagicMock()
    page.url = "http://router/webui/#/dhcp"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = _make_settings_mock(tmp_path)
    # Empty view: _eid_for_intent returns None → vision path tried → vision returns None
    # → first_match reached.
    fresh_view, fresh_map = _fresh_view_and_map()

    resolve_calls: list[int] = []

    def fake_resolve(*args, **kwargs):
        resolve_calls.append(1)
        return None

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", side_effect=fake_resolve),
        patch("backend.webui_agent.login.first_match", return_value=None) as mock_first_match,
        patch(
            "backend.webui_agent.semantic_dom.describe_page", return_value=(fresh_view, fresh_map)
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg=_base_intent_msg(),
            ev=ev,
        )

    # eid lookup tried first (silent — no call to assert, it's internal).
    # vision was tried second (returns None).
    assert len(resolve_calls) == 1, "vision must be called once when eid lookup returns None"
    # first_match IS called as the final fallback.
    mock_first_match.assert_called_once()
    assert reply["failure_reason"] == "unknown_eid"


def test_act_by_intent_evicts_and_retries_on_element_hidden(tmp_path):
    """First _do_act returns element_hidden → evict called, retry with new selector."""
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    selector_a = "input[aria-label='Network']"
    selector_b = "input[name='networkAddr']"

    page = MagicMock()
    page.url = "http://router/webui/#/dhcp"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = _make_settings_mock(tmp_path)
    fresh_view, fresh_map = _fresh_view_and_map()

    do_act_eids: list[str] = []

    def fake_do_act(page_, locator_map_, view_id_, msg_, ev_):
        eid = msg_["eid"]
        do_act_eids.append(eid)
        # First call (selector_a) → element_hidden; second (selector_b) → ok.
        if len(do_act_eids) == 1:
            return (
                {"ok": False, "failure_reason": "element_hidden", "attempts": 0},
                locator_map_,
                view_id_,
            )
        return ({"ok": True, "attempts": 0}, locator_map_, view_id_)

    with (
        patch(
            "backend.webui_agent.vision_fallback.resolve_via_vision",
            side_effect=[selector_a, selector_b],
        ),
        patch(
            "backend.webui_agent.vision_fallback.evict_from_selector_cache",
            return_value=True,
        ) as mock_evict,
        patch(
            "backend.webui_agent.semantic_dom.describe_page", return_value=(fresh_view, fresh_map)
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
        patch("backend.webui_agent._playwright_subprocess._do_act", side_effect=fake_do_act),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg=_base_intent_msg(),
            ev=ev,
        )

    # evict_from_selector_cache was called once.
    mock_evict.assert_called_once()
    # _do_act was called twice: first with selector_a's synthetic_eid, then selector_b's.
    assert len(do_act_eids) == 2
    # Both eids must be different (derived from different selectors).
    assert do_act_eids[0] != do_act_eids[1]
    assert reply["ok"] is True


def test_act_by_intent_does_not_evict_on_element_missing(tmp_path):
    """First _do_act returns element_missing (not a staleness signal) → evict NOT called."""
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    selector = "input[aria-label='Network']"
    page = MagicMock()
    page.url = "http://router/webui/#/dhcp"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = _make_settings_mock(tmp_path)
    fresh_view, fresh_map = _fresh_view_and_map()

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", return_value=selector),
        patch(
            "backend.webui_agent.vision_fallback.evict_from_selector_cache",
        ) as mock_evict,
        patch(
            "backend.webui_agent.semantic_dom.describe_page", return_value=(fresh_view, fresh_map)
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
        patch(
            "backend.webui_agent._playwright_subprocess._do_act",
            return_value=(
                {"ok": False, "failure_reason": "element_missing", "attempts": 0},
                {},
                "fresh",
            ),
        ),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg=_base_intent_msg(),
            ev=ev,
        )

    mock_evict.assert_not_called()
    assert reply["failure_reason"] == "element_missing"


def test_act_by_intent_evicts_and_retries_on_unknown_error(tmp_path):
    """14h-E regression: unknown_error MUST trigger cache eviction + vision retry.

    Live smoke act_20260523_48a212 proved a poisoned cache entry
    (button:has-text('Add') from a session before the uniqueness-prompt
    fix) kept failing with unknown_error. The narrower STALENESS set
    {element_hidden, disabled, intercepted} never evicted it — the cache
    stayed poisoned and re-served the bad selector every session.
    Including unknown_error self-heals the cache on click failures.
    """
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    page = MagicMock()
    page.url = "http://router/webui/#/dhcp"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = _make_settings_mock(tmp_path)
    fresh_view, fresh_map = _fresh_view_and_map()

    selector_a = "button:has-text('Add')"  # poisoned
    selector_b = "[aria-label='Add Pool']"  # corrected on retry

    do_act_replies = [
        ({"ok": False, "failure_reason": "unknown_error", "attempts": 0}, {}, "fresh"),
        ({"ok": True, "attempts": 0}, {}, "fresh"),
    ]

    with (
        patch(
            "backend.webui_agent.vision_fallback.resolve_via_vision",
            side_effect=[selector_a, selector_b],
        ),
        patch(
            "backend.webui_agent.vision_fallback.evict_from_selector_cache",
            return_value=True,
        ) as mock_evict,
        patch(
            "backend.webui_agent.semantic_dom.describe_page", return_value=(fresh_view, fresh_map)
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
        patch(
            "backend.webui_agent._playwright_subprocess._do_act",
            side_effect=do_act_replies,
        ),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg=_base_intent_msg(),
            ev=ev,
        )

    # Cache evicted exactly once (after the unknown_error failure).
    mock_evict.assert_called_once()
    # Retry succeeded with the new selector.
    assert reply["ok"] is True


def test_act_by_intent_no_infinite_retry_loop(tmp_path):
    """Both first and retry _do_act return element_hidden → only ONE retry (resolve_via_vision called 2× max)."""
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    selector_a = "input[aria-label='Network']"
    selector_b = "input[name='networkAddr']"

    page = MagicMock()
    page.url = "http://router/webui/#/dhcp"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = _make_settings_mock(tmp_path)
    fresh_view, fresh_map = _fresh_view_and_map()

    resolve_calls: list[int] = []

    def fake_resolve(page_, intent_, ev_, settings_):
        resolve_calls.append(1)
        return selector_a if len(resolve_calls) == 1 else selector_b

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision", side_effect=fake_resolve),
        patch(
            "backend.webui_agent.vision_fallback.evict_from_selector_cache",
            return_value=True,
        ),
        patch(
            "backend.webui_agent.semantic_dom.describe_page", return_value=(fresh_view, fresh_map)
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
        patch(
            "backend.webui_agent._playwright_subprocess._do_act",
            return_value=(
                {"ok": False, "failure_reason": "element_hidden", "attempts": 0},
                {},
                "fresh",
            ),
        ),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg=_base_intent_msg(),
            ev=ev,
        )

    # resolve_via_vision called at most 2× (initial + one retry).
    assert len(resolve_calls) <= 2
    # No further eviction or retry loop — reply is the result of attempt 2.
    assert reply["failure_reason"] == "element_hidden"


def test_act_by_intent_uses_eid_first_when_describe_has_match(tmp_path):
    """14h-F core invariant: eid forward-lookup wins when describe surfaces the element.

    Set up describe view with e_020 role=button name="Add" — the DHCP Add
    button case from live smoke act_20260523_589a83. Intent asks for
    {role: button, name: "Add"}. _eid_for_intent resolves to e_020, so
    chosen_loc is set WITHOUT calling vision or first_match.
    """
    from backend.webui_agent._playwright_subprocess import _do_act_by_intent

    page = MagicMock()
    page.url = "http://router/webui/#/dhcp"
    ev = MagicMock()
    ev.session_dir = "/tmp/evid"
    ev.vision_call_count = 0

    settings_mock = _make_settings_mock(tmp_path)

    # Describe view contains the Add button — exactly what the DHCP smoke saw.
    add_loc = MagicMock()
    add_loc.get_attribute.return_value = ""  # no aria-label (safe: "Add" is not deny-listed)
    add_loc.text_content.return_value = "Add"

    fresh_view = {
        "view_id": "dhcp_fresh",
        "url": "http://router/webui/#/dhcp",
        "title": "DHCP",
        "elements": [
            {"eid": "e_020", "role": "button", "name": "Add", "enabled": True},
        ],
        "modals": [],
        "errors": [],
    }
    fresh_map = {"e_020": add_loc}

    do_act_calls: list[dict] = []

    def fake_do_act(page_, locator_map_, view_id_, msg_, ev_):
        do_act_calls.append({"msg": msg_, "locator_map_keys": list(locator_map_.keys())})
        return ({"ok": True, "attempts": 0}, locator_map_, view_id_)

    with (
        patch("backend.webui_agent.vision_fallback.resolve_via_vision") as mock_vision,
        patch("backend.webui_agent.login.first_match") as mock_first_match,
        patch(
            "backend.webui_agent.semantic_dom.describe_page",
            return_value=(fresh_view, fresh_map),
        ),
        patch("backend.core.settings.get_settings", return_value=settings_mock),
        patch("backend.webui_agent._playwright_subprocess._do_act", side_effect=fake_do_act),
    ):
        reply, _new_map, _new_vid = _do_act_by_intent(
            page=page,
            locator_map={},
            current_view_id="any",
            msg={"intent": {"role": "button", "name": "Add", "action": "click", "value": None}},
            ev=ev,
        )

    # Core 14h-F invariant: eid path won — vision and first_match NOT called.
    mock_vision.assert_not_called()
    mock_first_match.assert_not_called()
    # _do_act was called with e_020 (eid forward-lookup result).
    assert len(do_act_calls) == 1
    assert do_act_calls[0]["msg"]["eid"] == "e_020"
    assert reply["ok"] is True
    assert reply["chosen_eid"] == "e_020"


# ---------------------------------------------------------------------------
# Per-action timeout constants — fail-fast budget for form actions
# ---------------------------------------------------------------------------


def test_timeout_constants_click_larger_than_form():
    """Click timeout must be >= form timeout.

    Click fires an XHR that may have already reached the router before
    Playwright reports a timeout — we never retry it and a slightly
    larger window reduces false positives.  Form actions (fill/select/
    check/hover) use a tighter budget so missing elements fail fast.
    """
    assert _sub_mod._ACT_TIMEOUT_CLICK_MS >= _sub_mod._ACT_TIMEOUT_FORM_MS
    # Sanity: neither value is absurdly large (>10 s would reintroduce the bug).
    assert _sub_mod._ACT_TIMEOUT_CLICK_MS <= 10_000
    assert _sub_mod._ACT_TIMEOUT_FORM_MS <= 10_000
    # And neither is suspiciously small (<1 s would cause healthy-page false positives).
    assert _sub_mod._ACT_TIMEOUT_CLICK_MS >= 1_000
    assert _sub_mod._ACT_TIMEOUT_FORM_MS >= 1_000


def test_invoke_action_click_uses_click_timeout():
    """_invoke_action must pass _ACT_TIMEOUT_CLICK_MS to locator.click."""
    loc = MagicMock()
    _sub_mod._invoke_action(loc, "click", None)
    loc.click.assert_called_once_with(timeout=_sub_mod._ACT_TIMEOUT_CLICK_MS)


def test_invoke_action_fill_uses_form_timeout():
    """_invoke_action must pass _ACT_TIMEOUT_FORM_MS to locator.fill."""
    loc = MagicMock()
    _sub_mod._invoke_action(loc, "fill", "192.168.1.1")
    loc.fill.assert_called_once_with("192.168.1.1", timeout=_sub_mod._ACT_TIMEOUT_FORM_MS)


def test_invoke_action_select_uses_form_timeout():
    """_invoke_action must pass _ACT_TIMEOUT_FORM_MS to locator.select_option."""
    loc = MagicMock()
    _sub_mod._invoke_action(loc, "select", "enabled")
    loc.select_option.assert_called_once_with("enabled", timeout=_sub_mod._ACT_TIMEOUT_FORM_MS)


def test_invoke_action_check_uses_form_timeout():
    """_invoke_action must pass _ACT_TIMEOUT_FORM_MS to locator.check."""
    loc = MagicMock()
    _sub_mod._invoke_action(loc, "check", None)
    loc.check.assert_called_once_with(timeout=_sub_mod._ACT_TIMEOUT_FORM_MS)


def test_invoke_action_hover_uses_form_timeout():
    """_invoke_action must pass _ACT_TIMEOUT_FORM_MS to locator.hover."""
    loc = MagicMock()
    _sub_mod._invoke_action(loc, "hover", None)
    loc.hover.assert_called_once_with(timeout=_sub_mod._ACT_TIMEOUT_FORM_MS)


def test_fill_timeout_is_strictly_bounded():
    """Regression guard: _ACT_TIMEOUT_FORM_MS must be <= 4000 ms.

    The DHCP smoke burned 50-67 s on a single missing-element fill because
    the prior form timeout was too long.  This constant is the primary
    control — tighten this assertion if the timeout is further reduced.
    """
    assert _sub_mod._ACT_TIMEOUT_FORM_MS <= 4_000, (
        f"_ACT_TIMEOUT_FORM_MS={_sub_mod._ACT_TIMEOUT_FORM_MS} exceeds 4000 ms "
        "— re-check DHCP smoke regression risk"
    )


# ---------------------------------------------------------------------------
# Kendo UI dropdown support — _is_kendo_listbox / _kendo_select / _invoke_action
# ---------------------------------------------------------------------------


def test_is_kendo_listbox_returns_true_for_listbox_role():
    """Locator with role='listbox' is identified as a Kendo widget."""
    loc = MagicMock()
    loc.get_attribute.return_value = "listbox"
    assert _sub_mod._is_kendo_listbox(loc) is True


def test_is_kendo_listbox_returns_false_for_other_roles():
    """Locators with other roles are not Kendo listboxes."""
    for role in ("combobox", "select", "textbox", "button", None, ""):
        loc = MagicMock()
        loc.get_attribute.return_value = role
        assert _sub_mod._is_kendo_listbox(loc) is False, f"role={role!r} should not be kendo"


def test_is_kendo_listbox_returns_false_on_exception():
    """get_attribute() exception is swallowed; falls back to False."""
    loc = MagicMock()
    loc.get_attribute.side_effect = RuntimeError("timeout")
    assert _sub_mod._is_kendo_listbox(loc) is False


def test_kendo_select_dispatches_js_and_logs_on_success():
    """_kendo_select calls locator.evaluate() with the JS + value and succeeds via strategy 1.

    Strategy 1 (widget API): evaluate returns ok=True → log + return immediately.
    The JS uses kendo.widgetInstance(...).trigger('change') not dispatchEvent —
    this is the widget-API approach, which replaced the bare dispatchEvent call.
    """
    loc = MagicMock()
    loc.evaluate.return_value = {
        "ok": True,
        "selected": "255.255.255.0",
    }

    _sub_mod._kendo_select(loc, "255.255.255.0")

    # evaluate called exactly once (strategy 1 succeeds; no fallthrough).
    loc.evaluate.assert_called_once()
    call_args = loc.evaluate.call_args
    js_arg = call_args.args[0]
    value_arg = call_args.args[1]
    # Strategy 1 JS uses listboxEl parameter and kendo widgetInstance API.
    assert "listboxEl" in js_arg, "JS must walk up from the listbox element"
    assert "kendo" in js_arg, "Strategy 1 JS must reference the kendo global"
    assert value_arg == "255.255.255.0"
    # Playwright click must NOT have been called (strategy 1 succeeded).
    loc.click.assert_not_called()


def test_kendo_select_value_not_in_options_falls_through_to_strategy2():
    """Strategy 1 matches by VALUE only, so a value-only 'value_not_in_options'
    is NOT a dead-end — it must fall through to strategy 2 (Playwright has_text
    click), which matches by display text. Only strategy 3 declares the hard
    dead-end. (Audit fix: strategy 1 used to raise ValueError here, which would
    surface as unknown_error on a field the text-based fallbacks could resolve.)
    """
    loc = MagicMock()
    page = MagicMock()
    loc.page = page
    # Strategy 1: value-only match failed.
    loc.evaluate.return_value = {
        "ok": False,
        "reason": "value_not_in_options",
        "available": ["255.255.255.0", "255.255.255.128"],
    }
    # Strategy 2: not already open; open-click + list-item click both succeed.
    loc.get_attribute.return_value = None  # no aria-expanded
    list_item_mock = MagicMock()
    page.locator.return_value = list_item_mock

    # Must NOT raise — strategy 2 resolves it by display text.
    _sub_mod._kendo_select(loc, "255.255.255.0")

    # Strategy 1 evaluated once, then fell through to strategy 2's clicks.
    assert loc.evaluate.call_count == 1
    loc.click.assert_called_once()
    list_item_mock.click.assert_called_once()


def test_kendo_select_raises_value_error_when_hidden_select_strategy_fails():
    """_kendo_select raises ValueError when all strategies fail (no value found).

    Strategy 1: kendo_unavailable. Strategy 2: structural error (not timeout).
    Strategy 3 JS returns ok=False → ValueError.
    """
    loc = MagicMock()
    page = MagicMock()
    loc.page = page
    # Strategy 1: kendo unavailable.
    # Strategy 3 evaluate returns ok=False (value not in backing select options).
    # Use side_effect list: first call → kendo_unavailable; second call → not_found.
    loc.evaluate.side_effect = [
        {"ok": False, "reason": "kendo_unavailable"},
        {"ok": False, "error": "value not in options. available: 255.255.255.0, 255.255.255.128"},
    ]
    # Strategy 2: click raises a structural RuntimeError → fall through.
    loc.get_attribute.return_value = None
    loc.click.side_effect = RuntimeError("element detached")

    with pytest.raises(ValueError, match="kendo_select failed"):
        _sub_mod._kendo_select(loc, "/32")


def test_kendo_select_raises_value_error_when_all_strategies_fail():
    """_kendo_select raises ValueError when all three strategies fail.

    New 3-strategy contract (chunk 1):
    - Strategy 1: evaluate() raises RuntimeError → caught, fall through.
    - Strategy 2: click() raises RuntimeError (non-timeout structural error) → fall through.
    - Strategy 3: evaluate() raises RuntimeError → wrapped as ValueError.
    The old test expected RuntimeError("JS evaluation failed") which was the
    single-strategy contract; the new contract surfaces ValueError from strategy 3.
    """
    loc = MagicMock()
    page = MagicMock()
    loc.page = page
    # All evaluate calls raise — strategy 1 caught+fallthrough, strategy 3 wraps to ValueError.
    loc.evaluate.side_effect = RuntimeError("page closed")
    # Strategy 2: aria-expanded not set, click raises a NON-timeout error → fall through.
    loc.get_attribute.return_value = None
    loc.click.side_effect = RuntimeError("element detached")

    with pytest.raises(ValueError, match="all strategies"):
        _sub_mod._kendo_select(loc, "255.255.255.0")


def test_invoke_action_select_routes_kendo_to_kendo_select():
    """When action='select' on a Kendo listbox, _kendo_select is used (not select_option)."""
    loc = MagicMock()
    # Simulate role='listbox'.
    loc.get_attribute.return_value = "listbox"
    loc.evaluate.return_value = {
        "ok": True,
        "selected": "255.255.255.0",
        "select_name": "subnetmaskOptions",
    }

    _sub_mod._invoke_action(loc, "select", "255.255.255.0")

    # evaluate() called (kendo path); select_option NEVER called.
    loc.evaluate.assert_called_once()
    loc.select_option.assert_not_called()


def test_invoke_action_select_uses_select_option_for_plain_select():
    """Plain <select> (role != 'listbox') goes through the standard select_option path."""
    loc = MagicMock()
    loc.get_attribute.return_value = "combobox"  # native <select> is mapped to combobox

    _sub_mod._invoke_action(loc, "select", "VLAN46")

    loc.select_option.assert_called_once_with("VLAN46", timeout=_sub_mod._ACT_TIMEOUT_FORM_MS)
    loc.evaluate.assert_not_called()


def test_invoke_action_select_uses_select_option_when_role_is_none():
    """If get_attribute returns None (no role attr), treat as plain select — safe default."""
    loc = MagicMock()
    loc.get_attribute.return_value = None

    _sub_mod._invoke_action(loc, "select", "option1")

    loc.select_option.assert_called_once_with("option1", timeout=_sub_mod._ACT_TIMEOUT_FORM_MS)
    loc.evaluate.assert_not_called()


def test_invoke_action_kendo_select_exception_propagates():
    """If _kendo_select hits a genuine dead-end (all 3 strategies fail), the
    ValueError propagates to the caller (which classifies it as unknown_error
    in _do_act).
    """
    loc = MagicMock()
    page = MagicMock()
    loc.page = page
    # _is_kendo_listbox reads get_attribute("role") → "listbox"; strategy 2 reads
    # get_attribute("aria-expanded") → None.
    loc.get_attribute.side_effect = lambda name, **kw: (
        "listbox" if name == "role" else None
    )
    # Strategy 1: kendo_unavailable → fall through. Strategy 3: ok=False → ValueError.
    loc.evaluate.side_effect = [
        {"ok": False, "reason": "kendo_unavailable"},
        {"ok": False, "error": "value not in options"},
    ]
    # Strategy 2: structural (non-timeout) error → fall through.
    loc.click.side_effect = RuntimeError("element detached")

    with pytest.raises(ValueError):
        _sub_mod._invoke_action(loc, "select", "bad_value")


def test_do_act_kendo_select_classifies_value_error_as_unknown_error():
    """A genuine dead-end ValueError from _kendo_select (all 3 strategies fail)
    surfaces as failure_reason=unknown_error in _do_act (the non-Playwright
    exception branch).
    """
    loc = _make_locator_for_act()
    # Drive a genuine dead-end: strategy 1 unavailable → strategy 2 structural
    # (non-timeout) error → strategy 3 ok=False → ValueError → unknown_error.
    loc.get_attribute.side_effect = lambda name, **kw: (
        "listbox" if name == "role" else None
    )
    loc.evaluate.side_effect = [
        {"ok": False, "reason": "kendo_unavailable"},
        {"ok": False, "error": "value not in options"},
    ]
    loc.click.side_effect = RuntimeError("element detached")

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
                "action": "select",
                "value": "/32",
            },
            ev=ev,
        )

    assert reply["ok"] is False
    assert reply["failure_reason"] == "unknown_error"
    assert reply["exc_type"] == "ValueError"


# ---------------------------------------------------------------------------
# Chunk 1 — 3-strategy _kendo_select + timeout taxonomy regression lock
# ---------------------------------------------------------------------------


def test_kendo_select_uses_widget_api_then_falls_back():
    """Strategy 1 (widget API) succeeds → no Playwright click attempted.

    Also verifies the fallback variant: when strategy 1 returns 'kendo_unavailable',
    strategy 2 (dom_click) is taken.
    """
    # --- Variant A: strategy 1 succeeds ---
    loc_a = MagicMock()
    # page attribute required by the new _kendo_select implementation.
    loc_a.page = MagicMock()
    # evaluate is called for strategy 1; return ok=True.
    loc_a.evaluate.return_value = {"ok": True, "selected": "255.255.255.0"}

    _sub_mod._kendo_select(loc_a, "255.255.255.0")

    # evaluate called once for strategy 1; Playwright click never reached.
    assert loc_a.evaluate.call_count == 1
    loc_a.click.assert_not_called()

    # --- Variant B: strategy 1 unavailable (kendo_unavailable) → strategy 2 (dom_click) ---
    loc_b = MagicMock()
    page_b = MagicMock()
    loc_b.page = page_b
    # Strategy 1: kendo_unavailable.
    loc_b.evaluate.return_value = {"ok": False, "reason": "kendo_unavailable"}
    # Strategy 2: aria-expanded not set (not already open) → click to open.
    loc_b.get_attribute.return_value = None  # no aria-expanded
    # page.locator("ul.k-list li.k-item", has_text=value).click succeeds.
    list_item_mock = MagicMock()
    page_b.locator.return_value = list_item_mock

    _sub_mod._kendo_select(loc_b, "255.255.255.128")

    # evaluate still called once for strategy 1 (unavailable response).
    assert loc_b.evaluate.call_count == 1
    # Strategy 2: the widget's click was called to open the dropdown.
    loc_b.click.assert_called_once()
    # And the list item was clicked.
    list_item_mock.click.assert_called_once()


def test_kendo_select_timeout_bubbles_as_intercepted_not_unknown_error():
    """THE REGRESSION LOCK: Chunk 1's primary correctness contract.

    When strategy 1 is unavailable (kendo_unavailable) and strategy 2's
    locator.click() raises playwright.sync_api.TimeoutError, the timeout
    MUST propagate OUT of _kendo_select uncaught, so _do_act classifies it
    as element_intercepted (retried once) — NOT unknown_error (not retried).

    Before chunk 1, _kendo_select wrapped the entire body in try/except and
    raised RuntimeError("kendo_select JS evaluation failed: ..."), which
    _do_act caught as a non-Playwright exception → unknown_error → no retry.
    """
    from backend.webui_agent._playwright_subprocess import _do_act

    # Build a Kendo listbox locator whose strategy-1 evaluate is unavailable
    # and strategy-2 click raises PlaywrightTimeoutError.
    loc = MagicMock()
    page = MagicMock()
    loc.page = page

    # _is_kendo_listbox checks get_attribute("role") → must return "listbox" to
    # route through _kendo_select. _kendo_select's strategy 2 checks
    # get_attribute("aria-expanded") → None (not already open). Use side_effect
    # to return the right value per call:
    #   call 1 (from _is_kendo_listbox): "role" → "listbox"
    #   call 2 (from _kendo_select strategy 2): "aria-expanded" → None
    #   call 3+ (retry attempt from _do_act): "role" → "listbox" again
    def _get_attr_side_effect(name, **kw):
        if name == "role":
            return "listbox"
        return None  # aria-expanded and anything else

    loc.get_attribute.side_effect = _get_attr_side_effect
    # Strategy 1: kendo_unavailable (first evaluate call).
    loc.evaluate.return_value = {"ok": False, "reason": "kendo_unavailable"}
    # click() raises TimeoutError — simulates a transient stall opening the dropdown.
    loc.click.side_effect = PlaywrightTimeoutError("Timeout opening Kendo dropdown")
    # is_visible / is_enabled used by _do_act self-heal probes after the timeout.
    loc.is_visible.return_value = True
    loc.is_enabled.return_value = True

    ev = MagicMock()
    ev.session_dir = "/tmp/evid"

    # After the timeout, _do_act re-describes to probe the element state.
    # Provide the same loc so the probe finds it (visible + enabled → intercepted).
    with _patched_describe_page(locator_map={"e_001": loc}):
        reply, _new_map, _new_vid = _do_act(
            page=page,
            locator_map={"e_001": loc},
            current_view_id="v",
            msg={
                "view_id": "v",
                "eid": "e_001",
                "action": "select",
                "value": "255.255.255.128",
            },
            ev=ev,
        )

    # THE CRITICAL ASSERTION — must be element_intercepted (not unknown_error).
    assert reply["failure_reason"] == "element_intercepted", (
        f"Expected element_intercepted, got {reply['failure_reason']!r}. "
        "PlaywrightTimeoutError from _kendo_select must propagate uncaught so "
        "_do_act classifies it as element_intercepted, not unknown_error."
    )
    assert reply["ok"] is False
    # Prove the retry actually fired: with max_attempts=2, strategy 2's open-click
    # is reached on BOTH the initial attempt and the post-intercept retry.
    assert loc.click.call_count == 2, (
        f"expected 2 open-click attempts (initial + one retry), got {loc.click.call_count}"
    )
    assert reply["attempts"] == 1
