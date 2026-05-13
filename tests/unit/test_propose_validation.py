"""Regression tests for audit-B2 — propose_* helpers validate inputs at
propose time, not at execute time.

Pre-fix, a hostile or malformed propose call would create an action_id
that could be APPROVE-ed, then 422 at execute time. Now: bad input is
rejected during the propose call (surfaces as `bad_parameters` from the
dispatcher), and no action_id is ever created for the bad input.

Includes the hostile-input cases the previous audit asked for but didn't
have regression coverage on: newline injection in hostnames, out-of-range
VLAN ids, malformed IPv4.
"""

from __future__ import annotations

import pytest

from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import _actions

# _clean_actions fixture is in tests/conftest.py (autouse).


# ---------------------------------------------------------------------------
# propose_set_hostname
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "r1\nenable secret leak",  # newline → command injection
        "r1; conf t",  # semicolon
        "1bad",  # starts with digit
        "way-too-long-" + "x" * 80,
        "",  # empty
        "name with space",
    ],
)
def test_propose_set_hostname_rejects_bad_input(bad_name):
    result = tr.execute_tool("propose_set_hostname", {"new_name": bad_name})
    assert result["error"] == "bad_parameters"
    # No action was registered.
    assert _actions == {}


def test_propose_set_hostname_accepts_valid_name():
    result = tr.execute_tool("propose_set_hostname", {"new_name": "LAB-R1"})
    assert result["status"] == "awaiting_approval"
    assert "action_id" in result


# ---------------------------------------------------------------------------
# propose_set_interface_ip — validates interface + ip + mask all at once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        # malformed IPv4
        {"interface": "Gi0/0/0", "ip": "10.0.0.999", "mask": "255.255.255.0"},
        # 0.0.0.0 interface IP
        {"interface": "Gi0/0/0", "ip": "0.0.0.0", "mask": "255.255.255.0"},
        # non-contiguous mask
        {"interface": "Gi0/0/0", "ip": "10.0.0.1", "mask": "255.0.255.0"},
        # 0.0.0.0 mask
        {"interface": "Gi0/0/0", "ip": "10.0.0.1", "mask": "0.0.0.0"},
        # interface name with a space (injection-ish)
        {"interface": "Gi0/0/0 ;reload", "ip": "10.0.0.1", "mask": "255.255.255.0"},
        # network address (host bits == 0 on a /24)
        {"interface": "Gi0/0/0", "ip": "10.0.0.0", "mask": "255.255.255.0"},
        # broadcast address (host bits == 1 on a /24)
        {"interface": "Gi0/0/0", "ip": "10.0.0.255", "mask": "255.255.255.0"},
    ],
)
def test_propose_set_interface_ip_rejects_bad_input(params):
    result = tr.execute_tool("propose_set_interface_ip", params)
    assert result["error"] == "bad_parameters", f"expected reject for {params}"
    assert _actions == {}


def test_propose_set_interface_ip_accepts_valid_host_ip():
    result = tr.execute_tool(
        "propose_set_interface_ip",
        {"interface": "Gi0/0/0", "ip": "10.0.0.1", "mask": "255.255.255.0"},
    )
    assert result["status"] == "awaiting_approval"


def test_propose_set_interface_ip_accepts_slash31_endpoints():
    """A /31 point-to-point has no broadcast — both addresses are valid hosts."""
    result = tr.execute_tool(
        "propose_set_interface_ip",
        {"interface": "Gi0/0/0", "ip": "10.0.0.0", "mask": "255.255.255.254"},
    )
    assert result["status"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# propose_set_access_vlan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"vlan_id": 0, "vlan_name": "OFFICE"},  # below range
        {"vlan_id": 4095, "vlan_name": "OFFICE"},  # above range (cap is 4094)
        {"vlan_id": -1, "vlan_name": "OFFICE"},
        {"vlan_id": "10", "vlan_name": "OFFICE"},  # wrong type
        {"vlan_id": True, "vlan_name": "OFFICE"},  # bool is not int
        {"vlan_id": 10, "vlan_name": "has space"},  # bad name
        {"vlan_id": 10, "vlan_name": ""},  # empty name
        {"vlan_id": 10, "vlan_name": "x" * 33},  # name too long
    ],
)
def test_propose_set_access_vlan_rejects_bad_input(params):
    result = tr.execute_tool("propose_set_access_vlan", params)
    assert result["error"] == "bad_parameters", f"expected reject for {params}"
    assert _actions == {}


def test_propose_set_access_vlan_accepts_valid():
    result = tr.execute_tool(
        "propose_set_access_vlan",
        {"vlan_id": 30, "vlan_name": "OFFICE"},
    )
    assert result["status"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# WebUI propose helpers — same validators apply
# ---------------------------------------------------------------------------


def test_propose_webui_set_hostname_rejects_bad_input():
    result = tr.execute_tool("propose_webui_set_hostname", {"new_name": "bad name with space"})
    assert result["error"] == "bad_parameters"
    assert _actions == {}


def test_propose_webui_add_access_vlan_rejects_bad_input():
    result = tr.execute_tool(
        "propose_webui_add_access_vlan",
        {"vlan_id": 99999, "vlan_name": "OFFICE"},
    )
    assert result["error"] == "bad_parameters"
    assert _actions == {}
