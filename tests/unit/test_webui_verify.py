"""Unit tests for backend.webui_agent.verify — CLI ground-truth checks."""

from __future__ import annotations

from unittest.mock import patch

from backend.webui_agent.verify import verify_hostname, verify_vlan_exists

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
# ---------------------------------------------------------------------------


def test_verify_vlan_true_when_id_present():
    rows = [
        {"vlan_id": "1",  "name": "default"},
        {"vlan_id": "10", "name": "MANAGEMENT"},
    ]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(10) is True


def test_verify_vlan_false_when_id_missing():
    rows = [{"vlan_id": "1", "name": "default"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(99) is False


def test_verify_vlan_with_name_matches_case_insensitively():
    rows = [{"vlan_id": "30", "name": "OFFICE"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(30, name="office") is True
        assert verify_vlan_exists(30, name="OFFICE") is True


def test_verify_vlan_name_mismatch_returns_false():
    rows = [{"vlan_id": "30", "name": "OFFICE"}]
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=rows):
        assert verify_vlan_exists(30, name="ENGINEERING") is False


def test_verify_vlan_handles_unparsed_string_output():
    """If TextFSM had no template, show_vlan_brief returns []
    (read_tools.show_vlan_brief converts non-list to empty)."""
    with patch("backend.webui_agent.verify.show_vlan_brief", return_value=[]):
        assert verify_vlan_exists(30) is False
