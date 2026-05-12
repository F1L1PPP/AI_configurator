"""Unit tests for backend.webui_agent.pages.hostname_page — mocked Page."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.webui_agent.pages.hostname_page import (
    HostnameFieldNotFound,
    HostnameNavigationError,
    HostnamePage,
)


def _loc(input_value: str = "c1111-lab") -> MagicMock:
    loc = MagicMock()
    loc.count.return_value = 1
    loc.first = loc
    loc.input_value.return_value = input_value
    return loc


def _zero_loc() -> MagicMock:
    loc = MagicMock()
    loc.count.return_value = 0
    return loc


def _page_with(strategies_to_locator: dict) -> MagicMock:
    """Build a mock Page that returns specific locators for specific calls.

    Strategies key shape: {label}, {role:name}, {locator:selector}.
    The mock also supplies a wait_for on every returned locator so the
    'wait for menu to render' preamble in HostnamePage.goto() resolves
    cleanly in unit tests.
    """
    page = MagicMock()

    def by_label(label, exact=False):
        return strategies_to_locator.get(f"label:{label}", _zero_loc())

    def by_role(role, name=None):
        return strategies_to_locator.get(f"role:{role}:{name}", _zero_loc())

    def by_locator(selector):
        return strategies_to_locator.get(f"locator:{selector}", _zero_loc())

    page.get_by_label = MagicMock(side_effect=by_label)
    page.get_by_role  = MagicMock(side_effect=by_role)
    page.locator      = MagicMock(side_effect=by_locator)
    page.wait_for_load_state = MagicMock()
    page.url = "https://192.168.10.1/admin"
    return page


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def test_goto_navigates_directly_to_hostname_route():
    """The new goto() bypasses the sidebar and hits /webui/#/general directly."""
    page = _page_with({"label:Host Name": _loc()})
    page.url = "https://192.168.10.1/webui/#/dashboard"

    hp = HostnamePage(page)
    hp.goto()

    # page.goto called with the /general route on the same host
    page.goto.assert_called_once()
    target_url = page.goto.call_args.args[0]
    assert target_url.startswith("https://192.168.10.1")
    assert target_url.endswith("/webui/#/general")


def test_goto_raises_when_form_not_found_after_nav():
    """If the direct route lands somewhere without the hostname input, raise
    with a clear message rather than failing later in set_hostname."""
    page = _page_with({})  # no label:'Host Name' match → no form
    page.url = "https://192.168.10.1/webui/#/dashboard"

    hp = HostnamePage(page)
    with pytest.raises(HostnameNavigationError, match="hostname form"):
        hp.goto()


def test_goto_extracts_base_url_from_current_page():
    """The base URL is reconstructed from the current page.url so this works
    against any router IP, not just the dev unit."""
    page = _page_with({"label:Host Name": _loc()})
    page.url = "https://10.20.30.40/webui/#/something"

    hp = HostnamePage(page)
    hp.goto()

    target_url = page.goto.call_args.args[0]
    assert target_url == "https://10.20.30.40/webui/#/general"


# ---------------------------------------------------------------------------
# Reading current hostname
# ---------------------------------------------------------------------------


def test_get_current_hostname_returns_input_value():
    hostname_loc = _loc(input_value="c1111-lab")
    page = _page_with({"label:Host Name": hostname_loc})
    hp = HostnamePage(page)
    assert hp.get_current_hostname() == "c1111-lab"


def test_get_current_hostname_raises_when_field_missing():
    page = _page_with({})
    hp = HostnamePage(page)
    with pytest.raises(HostnameFieldNotFound):
        hp.get_current_hostname()


# ---------------------------------------------------------------------------
# Filling the hostname field
# ---------------------------------------------------------------------------


def test_set_hostname_focuses_then_fills():
    hostname_loc = _loc()
    page = _page_with({"label:Host Name": hostname_loc})
    hp = HostnamePage(page)
    hp.set_hostname("LAB-R1")
    # click() focuses + triggers ng-focus, fill() clears+types+ng-change
    hostname_loc.click.assert_called_once()
    hostname_loc.fill.assert_called_once_with("LAB-R1")


# ---------------------------------------------------------------------------
# Apply button
# ---------------------------------------------------------------------------


def test_apply_clicks_the_apply_button():
    apply_loc = _loc()
    page = _page_with({"role:button:Apply": apply_loc})
    hp = HostnamePage(page)
    hp.apply()
    apply_loc.click.assert_called_once()


def test_apply_raises_when_button_missing():
    page = _page_with({})
    hp = HostnamePage(page)
    with pytest.raises(HostnameFieldNotFound, match="Apply"):
        hp.apply()
