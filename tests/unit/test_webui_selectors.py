"""Unit tests for backend.webui_agent.selectors — YAML map loader."""

from __future__ import annotations

import pytest

from backend.webui_agent.selectors import load_selectors


def test_load_default_map_returns_dict():
    sel = load_selectors()
    assert isinstance(sel, dict)


def test_default_map_has_login_section():
    sel = load_selectors()
    assert "login" in sel
    assert "username" in sel["login"]
    assert "password" in sel["login"]
    assert "submit" in sel["login"]


def test_login_username_strategies_are_a_list_of_dicts():
    sel = load_selectors()
    strategies = sel["login"]["username"]
    assert isinstance(strategies, list)
    assert len(strategies) >= 2  # at least primary + one fallback
    for s in strategies:
        assert isinstance(s, dict)
        # every strategy must declare at least one key Playwright can act on
        assert any(k in s for k in ("role", "label", "text", "css"))


def test_default_map_has_nav_section():
    sel = load_selectors()
    assert "nav" in sel
    for required_item in ("dashboard", "configuration", "administration"):
        assert required_item in sel["nav"]


def test_default_map_has_vlan_form_section():
    sel = load_selectors()
    assert "vlan_form" in sel
    for required_item in ("add_button", "vlan_id", "save_button", "cancel_button"):
        assert required_item in sel["vlan_form"]


def test_default_map_has_hostname_form_section():
    sel = load_selectors()
    assert "hostname_form" in sel
    assert "hostname_input" in sel["hostname_form"]
    assert "apply_button" in sel["hostname_form"]


def test_missing_map_raises():
    with pytest.raises(FileNotFoundError):
        load_selectors("does_not_exist")


def test_load_is_cached():
    """Two calls with the same name return the exact same dict (lru_cache)."""
    load_selectors.cache_clear()
    sel1 = load_selectors("iosxe_default")
    sel2 = load_selectors("iosxe_default")
    assert sel1 is sel2  # identity, not just equality → proves caching
