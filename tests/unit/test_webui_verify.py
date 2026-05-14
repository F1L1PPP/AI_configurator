"""Unit tests for backend.webui_agent.verify — CLI ground-truth checks."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.webui_agent.verify import verify_hostname, verify_vlan_exists

# All tests in this module exercise the WebUI agent layer (Playwright is
# mocked at the page-object level so no real browser launches). Tagged with
# the `webui` marker so `pytest -m 'not webui'` skips them during fast
# iteration on unrelated layers. Review §5 cleanup.
pytestmark = pytest.mark.webui

# ---------------------------------------------------------------------------
# verify_hostname
# ---------------------------------------------------------------------------


def test_verify_hostname_true_when_line_present():
    cfg = """\
!
version 17.6
hostname LAB-R1
!
"""
    with patch("backend.webui_agent.verify.show_running_config", return_value=cfg):
        assert verify_hostname("LAB-R1") is True


def test_verify_hostname_false_when_different_name():
    cfg = "hostname c1111-lab\n"
    with patch("backend.webui_agent.verify.show_running_config", return_value=cfg):
        assert verify_hostname("LAB-R1") is False


def test_verify_hostname_substring_match_is_rejected():
    """A regex on the full line — 'hostname LAB' must NOT match when actual
    config says 'hostname LAB-R1'."""
    cfg = "hostname LAB-R1\n"
    with patch("backend.webui_agent.verify.show_running_config", return_value=cfg):
        assert verify_hostname("LAB") is False  # not the full word
        assert verify_hostname("LAB-R1") is True


def test_verify_hostname_handles_leading_whitespace():
    """Some IOS XE configs indent the hostname line; our regex tolerates it."""
    cfg = "  hostname  LAB-R1  \n"
    with patch("backend.webui_agent.verify.show_running_config", return_value=cfg):
        assert verify_hostname("LAB-R1") is True


def test_verify_hostname_handles_regex_special_chars_in_name():
    """The expected name is regex-escaped, so dots/parens don't break matching."""
    cfg = "hostname my.weird.name\n"
    with patch("backend.webui_agent.verify.show_running_config", return_value=cfg):
        assert verify_hostname("my.weird.name") is True


# ---------------------------------------------------------------------------
# verify_vlan_exists
#
# Row-shape note: the ntc-templates `cisco_ios_show_vlan_brief` template
# emits rows with key `vlan_name` (NOT `name`). The original fixtures in
# this file used `name`, which masked a real production bug: every
# WebUI-VLAN-add post-write check failed with "name mismatch" because
# verify_vlan_exists looked for the wrong key. All fixtures now use the
# real key, plus a `test_verify_vlan_ignores_legacy_name_key`
# regression test that hard-pins the field name.
# ---------------------------------------------------------------------------


def test_verify_vlan_true_when_id_present():
    rows = [
        {"vlan_id": "1", "vlan_name": "default"},
        {"vlan_id": "10", "vlan_name": "MANAGEMENT"},
    ]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(10) is True


def test_verify_vlan_false_when_id_missing():
    rows = [{"vlan_id": "1", "vlan_name": "default"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(99) is False


def test_verify_vlan_with_name_matches_case_insensitively():
    rows = [{"vlan_id": "30", "vlan_name": "OFFICE"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(30, name="office") is True
        assert verify_vlan_exists(30, name="OFFICE") is True


def test_verify_vlan_name_mismatch_returns_false():
    rows = [{"vlan_id": "30", "vlan_name": "OFFICE"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(30, name="ENGINEERING") is False


def test_verify_vlan_returns_false_when_show_returns_empty():
    """Empty rows (no template / no VLANs configured) → not found → False."""
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=[]):
        assert verify_vlan_exists(30) is False


def test_verify_vlan_ignores_legacy_name_key():
    """Hard pin on the ntc-templates field name (`vlan_name`).

    Regression for the production bug where every WebUI VLAN add was
    flagged as a name mismatch because verify_vlan_exists looked for
    `name` and the parsed row had `vlan_name`. If anyone ever flips
    the key back to `name`, this test breaks.
    """
    # Row uses the WRONG key — what the buggy code expected. The
    # verifier must still detect the mismatch (because vlan_name is
    # absent → falsy → can't equal "OFFICE").
    rows_with_wrong_key = [{"vlan_id": "30", "name": "OFFICE"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows_with_wrong_key):
        # ID matched, but the name field is missing under the right key.
        assert verify_vlan_exists(30, name="OFFICE") is False

    # And the same row under the CORRECT key matches.
    rows_with_right_key = [{"vlan_id": "30", "vlan_name": "OFFICE"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows_with_right_key):
        assert verify_vlan_exists(30, name="OFFICE") is True


def test_verify_vlan_handles_none_name_field():
    """ntc-templates can emit `vlan_name: None` for rows that don't have a
    name set. We must not crash with AttributeError on None.upper()."""
    rows = [{"vlan_id": "30", "vlan_name": None}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        # Without expected name → just confirms the ID exists → True
        assert verify_vlan_exists(30) is True
        # With expected name → can't match None → False, no crash
        assert verify_vlan_exists(30, name="OFFICE") is False
