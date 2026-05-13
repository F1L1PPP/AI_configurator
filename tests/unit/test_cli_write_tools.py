"""Unit tests for cli_agent.write_tools — mocked SSH, no real device."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

import backend.cli_agent.write_tools as wt
from backend.orchestration.confirmations import (
    NotApproved,
    approve_action,
    propose_action,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# _clean_actions fixture is now in tests/conftest.py (autouse).


@pytest.fixture(autouse=True)
def _mock_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_conn = MagicMock()
    mock_conn.send_config_set.return_value = "config applied"
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    monkeypatch.setattr(wt, "pool", mock_pool)
    return mock_conn


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch: pytest.MonkeyPatch):
    fake = MagicMock()
    fake.router_host = "10.0.0.1"
    fake.router_ssh_user = "admin"
    fake.router_ssh_password = "pass"
    fake.artifacts_dir = Path("artifacts")
    monkeypatch.setattr(wt, "get_settings", lambda: fake)


@pytest.fixture()
def _mock_snapshot(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(return_value=Path("artifacts/device-snapshots/test/pre"))
    monkeypatch.setattr(wt, "take_snapshot", mock)
    return mock


# ---------------------------------------------------------------------------
# set_hostname — approval gate
# ---------------------------------------------------------------------------


def test_set_hostname_refuses_without_approval(_mock_snapshot):
    action_id = propose_action("set_hostname", {"name": "LAB-R1"})
    with pytest.raises(NotApproved):
        wt.set_hostname("LAB-R1", action_id=action_id)


def test_set_hostname_never_touches_device_when_not_approved(_mock_pool, _mock_snapshot):
    action_id = propose_action("set_hostname", {"name": "LAB-R1"})
    with pytest.raises(NotApproved):
        wt.set_hostname("LAB-R1", action_id=action_id)
    _mock_pool.send_config_set.assert_not_called()


# ---------------------------------------------------------------------------
# set_hostname — snapshot ordering
# ---------------------------------------------------------------------------


def test_set_hostname_takes_pre_then_post_snapshot(_mock_pool, _mock_snapshot):
    action_id = propose_action("set_hostname", {"name": "R1"})
    approve_action(action_id)
    wt.set_hostname("R1", action_id=action_id)
    calls = _mock_snapshot.call_args_list
    assert calls[0] == call(action_id, "pre")
    assert calls[1] == call(action_id, "post")


def test_set_hostname_pre_snapshot_fires_before_config_push(_mock_pool, _mock_snapshot):
    """Verify snapshot is called before send_config_set."""
    call_order = []
    _mock_snapshot.side_effect = lambda *a: call_order.append("snapshot") or Path("x")
    _mock_pool.send_config_set.side_effect = lambda *a, **kw: call_order.append("config") or "ok"

    action_id = propose_action("set_hostname", {"name": "R1"})
    approve_action(action_id)
    wt.set_hostname("R1", action_id=action_id)

    assert call_order[0] == "snapshot"  # pre fires first
    assert call_order[1] == "config"


# ---------------------------------------------------------------------------
# set_hostname — config command
# ---------------------------------------------------------------------------


def test_set_hostname_sends_correct_command(_mock_pool, _mock_snapshot):
    action_id = propose_action("set_hostname", {"name": "NEW-NAME"})
    approve_action(action_id)
    wt.set_hostname("NEW-NAME", action_id=action_id)
    cmd_list = _mock_pool.send_config_set.call_args.args[0]
    assert any("hostname NEW-NAME" in c for c in cmd_list)


def test_set_hostname_returns_snapshot_paths(_mock_pool, _mock_snapshot):
    _mock_snapshot.side_effect = [
        Path("artifacts/snap/pre"),
        Path("artifacts/snap/post"),
    ]
    action_id = propose_action("set_hostname", {"name": "R1"})
    approve_action(action_id)
    result = wt.set_hostname("R1", action_id=action_id)
    assert "snapshot_pre" in result
    assert "snapshot_post" in result


# ---------------------------------------------------------------------------
# set_interface_ip — approval gate + command shape
# ---------------------------------------------------------------------------


def test_set_interface_ip_refuses_without_approval(_mock_snapshot):
    action_id = propose_action("set_interface_ip", {})
    with pytest.raises(NotApproved):
        wt.set_interface_ip("Gi0/0/0", "10.0.0.1", "255.255.255.0", action_id=action_id)


def test_set_interface_ip_sends_correct_commands(_mock_pool, _mock_snapshot):
    """Must send: interface X / no switchport / ip address Y Z / no shutdown.

    `no switchport` is needed because C1111-4P Gi0/1/0..Gi0/1/3 are L2
    switchports by default and IOS rejects `ip address` on a switchport
    ("% Invalid input detected"). On a routed port like Gi0/0/0 the
    command is a no-op — safe to send unconditionally.
    """
    action_id = propose_action("set_interface_ip", {})
    approve_action(action_id)
    wt.set_interface_ip("GigabitEthernet0/1/2", "10.1.1.1", "255.255.255.0", action_id=action_id)
    cmd_list = _mock_pool.send_config_set.call_args.args[0]
    joined = " ".join(cmd_list)
    assert "GigabitEthernet0/1/2" in joined
    assert "no switchport" in joined
    assert "10.1.1.1" in joined
    assert "255.255.255.0" in joined
    assert "no shutdown" in joined
    # Order matters: `no switchport` must come BEFORE `ip address` or the
    # IP command still hits the L2 port and errors out.
    no_switch_idx = next(i for i, c in enumerate(cmd_list) if "no switchport" in c)
    ip_idx = next(i for i, c in enumerate(cmd_list) if "ip address" in c)
    assert no_switch_idx < ip_idx, (
        f"`no switchport` (idx {no_switch_idx}) must precede `ip address` "
        f"(idx {ip_idx}); cmd_list={cmd_list}"
    )


# ---------------------------------------------------------------------------
# Input validation — audit #2 and #3 (command injection)
# ---------------------------------------------------------------------------


def test_set_hostname_rejects_newline_injection(_mock_pool, _mock_snapshot):
    """A hostile new_name containing \\n would smuggle extra IOS commands.
    Must raise ValueError BEFORE _guard / Netmiko."""
    with pytest.raises(ValueError, match="invalid hostname"):
        wt.set_hostname("r1\n enable password pwn", action_id="anything")
    _mock_pool.send_config_set.assert_not_called()


def test_set_hostname_rejects_special_chars(_mock_pool, _mock_snapshot):
    for hostile in (
        "hostname with spaces",
        "name; reload",
        "x?",
        "-leading-hyphen",
        "1numericstart",
        "a" * 64,
        "",
    ):
        with pytest.raises(ValueError, match="invalid hostname"):
            wt.set_hostname(hostile, action_id="anything")


def test_set_hostname_accepts_valid_names():
    """Validation must permit the actual hostnames we use in demos."""
    for ok in ("LAB-R1", "c1111-lab", "a", "A1", "node-42-x-9", "Z" * 63):
        wt._validate_hostname(ok)  # no exception


def test_set_interface_ip_rejects_bad_ip(_mock_pool, _mock_snapshot):
    with pytest.raises(ValueError, match="invalid IPv4 address"):
        wt.set_interface_ip("Gi0/0/0", "999.999.999.999", "255.255.255.0", action_id="anything")
    _mock_pool.send_config_set.assert_not_called()


def test_set_interface_ip_rejects_bad_mask(_mock_pool, _mock_snapshot):
    # Bad mask now goes through _validate_subnet_mask, which uses a
    # different error prefix than the legacy _validate_ipv4 message.
    with pytest.raises(ValueError, match="invalid subnet mask"):
        wt.set_interface_ip("Gi0/0/0", "10.0.0.1", "not-a-mask", action_id="anything")


def test_set_interface_ip_rejects_bad_interface(_mock_pool, _mock_snapshot):
    """Interface names with shell metacharacters or newlines must be rejected."""
    for hostile in (
        "Gi0/0/0\n no shutdown\n config terminal",
        "Gi 0/0/0",  # space
        "x" * 32,  # too long
        "",
    ):
        with pytest.raises(ValueError, match="invalid interface name"):
            wt.set_interface_ip(hostile, "10.0.0.1", "255.255.255.0", action_id="anything")


# ---------------------------------------------------------------------------
# set_access_vlan
# ---------------------------------------------------------------------------


def test_set_access_vlan_refuses_without_approval(_mock_snapshot):
    aid = propose_action("set_access_vlan", {"vlan_id": 40, "vlan_name": "OFFICE"})
    with pytest.raises(NotApproved):
        wt.set_access_vlan(40, "OFFICE", action_id=aid)


def test_set_access_vlan_sends_correct_commands(_mock_pool, _mock_snapshot):
    aid = propose_action("set_access_vlan", {"vlan_id": 40, "vlan_name": "OFFICE"})
    approve_action(aid)
    result = wt.set_access_vlan(40, "OFFICE", action_id=aid)
    _mock_pool.send_config_set.assert_called_once()
    cmds = _mock_pool.send_config_set.call_args.args[0]
    assert "vlan 40" in cmds
    assert " name OFFICE" in cmds
    assert result["tool"] == "set_access_vlan"
    assert result["params"] == {"vlan_id": 40, "vlan_name": "OFFICE"}


def test_set_access_vlan_takes_pre_then_post_snapshot(_mock_pool, _mock_snapshot):
    aid = propose_action("set_access_vlan", {"vlan_id": 40, "vlan_name": "OFFICE"})
    approve_action(aid)
    wt.set_access_vlan(40, "OFFICE", action_id=aid)
    assert _mock_snapshot.call_args_list[0] == call(aid, "pre")
    assert _mock_snapshot.call_args_list[1] == call(aid, "post")


def test_set_access_vlan_rejects_out_of_range_id(_mock_pool, _mock_snapshot):
    for bad in (0, -1, 4095, 9999):
        with pytest.raises(ValueError, match="invalid VLAN id"):
            wt.set_access_vlan(bad, "OFFICE", action_id="anything")
    _mock_pool.send_config_set.assert_not_called()


def test_set_access_vlan_rejects_bool_id(_mock_pool, _mock_snapshot):
    """bool is a subclass of int — guard against True/False being accepted."""
    with pytest.raises(ValueError, match="invalid VLAN id"):
        wt.set_access_vlan(True, "OFFICE", action_id="anything")  # type: ignore[arg-type]


def test_set_access_vlan_rejects_injection_in_name(_mock_pool, _mock_snapshot):
    """VLAN names with newlines / spaces / shell chars must be rejected before SSH."""
    for hostile in (
        "OFFICE\n shutdown",
        "OFFICE 2",
        "office;rm",
        "x" * 33,
        "",
    ):
        with pytest.raises(ValueError, match="invalid VLAN name"):
            wt.set_access_vlan(40, hostile, action_id="anything")
    _mock_pool.send_config_set.assert_not_called()


def test_set_access_vlan_accepts_valid_names(_mock_pool, _mock_snapshot):
    aid = propose_action("set_access_vlan", {"vlan_id": 40, "vlan_name": "OFFICE"})
    approve_action(aid)
    for ok in ("OFFICE", "lab-vlan-1", "DMZ_INTERNAL", "v" * 32):
        wt._validate_vlan_name(ok)  # no exception
