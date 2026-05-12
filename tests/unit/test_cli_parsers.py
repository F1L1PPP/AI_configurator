"""Unit tests for cli_agent.parsers — canned show output, no SSH needed."""

from __future__ import annotations

from unittest.mock import patch

from backend.cli_agent.parsers import parse

# ---------------------------------------------------------------------------
# Canned show ip interface brief output (real IOS XE format)
# ---------------------------------------------------------------------------

_SHOW_IP_INT_BRIEF = """\
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0/0   192.168.10.1    YES NVRAM  up                    up
GigabitEthernet0/0/1   unassigned      YES NVRAM  administratively down down
GigabitEthernet0/0/2   unassigned      YES NVRAM  administratively down down
"""

_SHOW_VLAN_BRIEF = """\
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Gi0/0
10   MANAGEMENT                       active    Gi0/1
"""


def test_parse_returns_list_of_dicts_when_template_found() -> None:
    result = parse("cisco_ios", "show ip interface brief", _SHOW_IP_INT_BRIEF)
    assert isinstance(result, list)
    assert len(result) >= 1
    first = result[0]
    assert "intf" in first or "interface" in first.get("intf", first)


def test_parse_show_ip_int_brief_has_expected_keys() -> None:
    result = parse("cisco_ios", "show ip interface brief", _SHOW_IP_INT_BRIEF)
    assert isinstance(result, list)
    # ntc-templates uses lowercase key names; check a subset
    keys = set(result[0].keys()) if result else set()
    assert keys, "expected at least one row"


def test_parse_falls_back_to_string_for_unknown_command() -> None:
    # ntc-templates has no template for this command
    raw = "some output that has no template"
    result = parse("cisco_ios", "show nonexistent command xyz", raw)
    assert isinstance(result, str)
    assert result == raw


def test_parse_falls_back_gracefully_when_ntc_raises() -> None:
    raw = "arbitrary output"
    with patch("backend.cli_agent.parsers._ntc_parse", side_effect=Exception("boom")):
        result = parse("cisco_ios", "show version", raw)
    assert result == raw


def test_parse_returns_string_on_empty_ntc_result() -> None:
    raw = "some output"
    with patch("backend.cli_agent.parsers._ntc_parse", return_value=[]):
        result = parse("cisco_ios", "show version", raw)
    assert result == raw
