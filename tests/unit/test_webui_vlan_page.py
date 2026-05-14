"""Unit tests for backend.webui_agent.pages.vlan_page — selector chains mocked."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import backend.webui_agent.pages.vlan_page as vp_mod
from backend.webui_agent.pages.vlan_page import (
    VlanFieldNotFound,
    VlanPage,
)

# All tests in this module exercise the WebUI agent layer (Playwright is
# mocked at the page-object level so no real browser launches). Tagged with
# the `webui` marker so `pytest -m 'not webui'` skips them during fast
# iteration on unrelated layers. Review §5 cleanup.
pytestmark = pytest.mark.webui


@pytest.fixture()
def page() -> MagicMock:
    """A mock Playwright Page with .locator/.get_by_role/etc. chained MagicMocks."""
    p = MagicMock()
    p.url = "https://192.168.10.1/webui/#/dashboard"
    return p


@pytest.fixture()
def stub_networkidle(monkeypatch: pytest.MonkeyPatch):
    """Avoid real Playwright waits in unit tests."""
    monkeypatch.setattr(vp_mod, "wait_for_networkidle", MagicMock())


def test_init_loads_selectors(page: MagicMock):
    vp = VlanPage(page)
    assert "vlan_form" in vp._sel
    assert "vlan_nav" in vp._sel
    assert "add_button" in vp._sel["vlan_form"]
    assert "save_button" in vp._sel["vlan_form"]


def test_click_add_raises_when_button_missing(
    page: MagicMock, stub_networkidle, monkeypatch: pytest.MonkeyPatch
):
    """If first_match returns None for the add button, click_add must raise
    VlanFieldNotFound (no silent miss)."""
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=None))
    vp = VlanPage(page)
    with pytest.raises(VlanFieldNotFound):
        vp.click_add()


def test_click_add_clicks_button_when_present(
    page: MagicMock, stub_networkidle, monkeypatch: pytest.MonkeyPatch
):
    btn = MagicMock()
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=btn))
    vp = VlanPage(page)
    vp.click_add()
    btn.click.assert_called_once()


def test_set_vlan_id_fills_field_with_string_repr(page: MagicMock, monkeypatch: pytest.MonkeyPatch):
    """vlan_id is int in the API but must be filled as str (Playwright .fill expects str)."""
    loc = MagicMock()
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=loc))
    vp = VlanPage(page)
    vp.set_vlan_id(30)
    loc.click.assert_called_once()
    loc.fill.assert_called_once_with("30")


def test_set_vlan_id_raises_when_field_missing(page: MagicMock, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=None))
    vp = VlanPage(page)
    with pytest.raises(VlanFieldNotFound):
        vp.set_vlan_id(30)


def test_set_vlan_name_skips_silently_when_field_absent(
    page: MagicMock, monkeypatch: pytest.MonkeyPatch
):
    """Some IOS XE builds omit the Name field. set_vlan_name must not raise —
    just log and skip, per the playground script's observation."""
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=None))
    vp = VlanPage(page)
    vp.set_vlan_name("OFFICE")  # should NOT raise


def test_set_vlan_name_fills_when_present(page: MagicMock, monkeypatch: pytest.MonkeyPatch):
    loc = MagicMock()
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=loc))
    vp = VlanPage(page)
    vp.set_vlan_name("OFFICE")
    loc.fill.assert_called_once_with("OFFICE")


def test_save_raises_when_button_missing(
    page: MagicMock, stub_networkidle, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=None))
    vp = VlanPage(page)
    with pytest.raises(VlanFieldNotFound):
        vp.save()


def test_save_clicks_when_present(
    page: MagicMock, stub_networkidle, monkeypatch: pytest.MonkeyPatch
):
    btn = MagicMock()
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=btn))
    vp = VlanPage(page)
    vp.save()
    btn.click.assert_called_once()


def test_goto_uses_direct_hash_route(
    page: MagicMock, stub_networkidle, monkeypatch: pytest.MonkeyPatch
):
    """goto() must navigate via /webui/#/vlan, bypassing the sidebar.

    The Cisco IOS XE 17.6.3a sidebar renders unreliably under Playwright
    (Day 5 hostname fix). VLAN flow uses the same direct-hash-route
    pattern.
    """
    page.url = "https://192.168.10.1/webui/#/dashboard"
    # first_match returns the VLAN tab locator (None would also be OK —
    # _select_vlan_tab logs + continues without raising).
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=MagicMock()))
    vp = VlanPage(page)
    vp.goto()
    # Assert page.goto was called with the hash-route URL, not by clicking
    # the sidebar.
    page.goto.assert_called_once()
    called_url = page.goto.call_args.args[0]
    assert called_url == "https://192.168.10.1/webui/#/vlan"


def test_goto_continues_when_vlan_tab_not_found(
    page: MagicMock, stub_networkidle, monkeypatch: pytest.MonkeyPatch
):
    """If the VLAN tab can't be located, goto() must NOT raise — some IOS
    XE builds may render the VLAN table without a tab strip. click_add()
    will surface a clearer error if the page state is actually wrong."""
    page.url = "https://192.168.10.1/webui/#/dashboard"
    monkeypatch.setattr(vp_mod, "first_match", MagicMock(return_value=None))
    vp = VlanPage(page)
    vp.goto()  # should not raise


def test_dump_diagnostics_does_not_shadow_method_signature(
    page: MagicMock,
):
    """Regression: _dump_diagnostics uses `lbl` not `label` for loop variable
    (Copilot finding fix). Calling it should not raise even with the param
    name `label` shadowed by what was previously the loop variable."""
    page.locator.return_value.inner_text.return_value = "body text"
    page.locator.return_value.count.return_value = 0
    page.locator.return_value.all.return_value = []
    vp = VlanPage(page)
    vp._dump_diagnostics("test-label")  # should not raise
