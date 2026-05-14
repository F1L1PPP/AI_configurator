"""Unit tests for backend.webui_agent.login — strategy resolver + login flow.

Playwright is mocked at the Page-object level so no browser launches.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.webui_agent.login import (
    _build,
    first_match,
    is_session_expired,
    login,
)

# All tests in this module exercise the WebUI agent layer (Playwright is
# mocked at the page-object level so no real browser launches). Tagged with
# the `webui` marker so `pytest -m 'not webui'` skips them during fast
# iteration on unrelated layers. Review §5 cleanup.
pytestmark = pytest.mark.webui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _loc(count: int = 1) -> MagicMock:
    """Mock locator. `first_match` walks via .nth(i) and checks .is_visible,
    so the inner mock must report visible."""
    loc = MagicMock()
    loc.count.return_value = count
    inner = MagicMock()
    inner.is_visible = MagicMock(return_value=True)
    loc.first = inner
    loc.nth = MagicMock(return_value=inner)
    return loc


def _page_with_strategy_results(results_by_strategy: dict[str, int]) -> MagicMock:
    """Build a mock Page where each strategy keyed by its repr returns the
    locator with the specified count. Unmapped strategies return count=0."""
    page = MagicMock()

    def fake_get_by_role(role: str, name: str | None = None):
        key = f"role:{role}:{name}"
        return _loc(results_by_strategy.get(key, 0))

    def fake_get_by_label(label: str, exact: bool = False):
        return _loc(results_by_strategy.get(f"label:{label}", 0))

    def fake_locator(selector: str):
        return _loc(results_by_strategy.get(f"locator:{selector}", 0))

    page.get_by_role = MagicMock(side_effect=fake_get_by_role)
    page.get_by_label = MagicMock(side_effect=fake_get_by_label)
    page.locator = MagicMock(side_effect=fake_locator)
    return page


# ---------------------------------------------------------------------------
# _build — each strategy key maps to the right Playwright call
# ---------------------------------------------------------------------------


def test_build_role_with_name():
    page = MagicMock()
    _build(page, {"role": "button", "name": "Log In"})
    page.get_by_role.assert_called_once_with("button", name="Log In")


def test_build_role_without_name():
    page = MagicMock()
    _build(page, {"role": "textbox"})
    page.get_by_role.assert_called_once_with("textbox")


def test_build_label():
    page = MagicMock()
    _build(page, {"label": "Username"})
    page.get_by_label.assert_called_once_with("Username", exact=False)


def test_build_text_wraps_in_text_prefix():
    page = MagicMock()
    _build(page, {"text": "/^VLAN$/i"})
    page.locator.assert_called_once_with("text=/^VLAN$/i")


def test_build_css_passes_through():
    page = MagicMock()
    _build(page, {"css": "input[name='username']"})
    page.locator.assert_called_once_with("input[name='username']")


def test_build_returns_none_for_unknown_strategy():
    page = MagicMock()
    assert _build(page, {"unsupported_key": "x"}) is None


# ---------------------------------------------------------------------------
# first_match — walks the chain, returns first hit
# ---------------------------------------------------------------------------


def test_first_match_returns_primary_when_it_resolves():
    page = _page_with_strategy_results({"label:Username": 1})
    strategies = [{"label": "Username"}, {"css": "input[name='username']"}]
    result = first_match(page, strategies)
    assert result is not None


def test_first_match_falls_back_to_secondary():
    page = _page_with_strategy_results({"locator:input[name='username']": 1})
    strategies = [{"label": "Username"}, {"css": "input[name='username']"}]
    result = first_match(page, strategies)
    assert result is not None


def test_first_match_returns_none_when_nothing_resolves():
    page = _page_with_strategy_results({})  # everything returns count=0
    strategies = [{"label": "Username"}, {"css": "x"}]
    result = first_match(page, strategies)
    assert result is None


def test_first_match_survives_exception_in_one_strategy():
    """A strategy that throws should be skipped, not abort the chain."""
    page = MagicMock()
    # First strategy raises, second works
    page.get_by_role.side_effect = Exception("boom")
    page.locator.return_value = _loc(1)
    strategies = [{"role": "button", "name": "X"}, {"css": "button.fallback"}]
    result = first_match(page, strategies)
    assert result is not None


# ---------------------------------------------------------------------------
# login() — high-level flow with mocked page + real selectors yaml
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mock_settings(monkeypatch: pytest.MonkeyPatch):
    import backend.webui_agent.login as login_mod

    fake = MagicMock()
    fake.router_webui_base_url = "https://10.0.0.1"
    fake.router_webui_user = "testuser"
    fake.router_webui_password = "testpass"
    monkeypatch.setattr(login_mod, "get_settings", lambda: fake)


def test_login_fails_when_username_field_missing(_mock_settings):
    page = _page_with_strategy_results({})  # no strategy resolves
    page.goto = MagicMock()
    page.wait_for_load_state = MagicMock()
    page.keyboard = MagicMock()

    result = login(page)
    assert result is False
    page.goto.assert_called_once()


def test_login_success_with_first_strategy_match(_mock_settings):
    page = _page_with_strategy_results(
        {
            "label:Username": 1,
            "label:Password": 1,
            "role:button:Log In": 1,
        }
    )
    page.goto = MagicMock()
    page.wait_for_load_state = MagicMock()
    page.keyboard = MagicMock()
    page.url = "https://10.0.0.1/dashboard"

    result = login(page)
    assert result is True


def test_login_falls_back_to_enter_when_no_submit_button(_mock_settings):
    page = _page_with_strategy_results(
        {
            "label:Username": 1,
            "label:Password": 1,
            # no submit strategy hits
        }
    )
    page.goto = MagicMock()
    page.wait_for_load_state = MagicMock()
    page.keyboard = MagicMock()
    page.url = "https://10.0.0.1/dashboard"

    result = login(page)
    assert result is True
    page.keyboard.press.assert_called_once_with("Enter")


# ---------------------------------------------------------------------------
# is_session_expired
# ---------------------------------------------------------------------------


def test_is_session_expired_true_when_login_form_visible():
    page = _page_with_strategy_results({"label:Username": 1})
    assert is_session_expired(page) is True


def test_is_session_expired_false_when_no_login_form():
    page = _page_with_strategy_results({})
    assert is_session_expired(page) is False
