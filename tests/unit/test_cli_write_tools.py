"""Unit tests for cli_agent.write_tools — mocked SSH, no real device."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

import backend.cli_agent.write_tools as wt
from backend.orchestration.confirmations import (
    NotApproved,
    approve_action,
    get_action,
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
    # Default verify-read output satisfies the post-write `_verify_running_config`
    # patterns for set_hostname / set_interface_ip / set_access_vlan happy-path
    # tests below. Tests that exercise verify-miss override this per-test.
    mock_conn.send_command.return_value = (
        "hostname R1\n"
        "hostname NEW-NAME\n"
        "hostname LAB-R1\n"
        "interface GigabitEthernet0/1/2\n"
        " no switchport\n"
        " ip address 10.1.1.1 255.255.255.0\n"
        " no shutdown\n"
        "40   OFFICE                           active\n"
    )
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


# ---------------------------------------------------------------------------
# Post-write validation — % error scanner + show-back verify
# (Regression suite for the 2026-05-18 silent-failure bug: `set_interface_ip`
# on Gi0/1/3 returned success but the IP never landed because the hardware-
# L2 port silently rejected `ip address`. Until write_tools validate their
# own work, every fast-path CLI write is suspect.)
# ---------------------------------------------------------------------------


def test_check_netmiko_output_for_errors_passes_clean_output():
    """No '%' lines → returns None, no exception."""
    wt._check_netmiko_output_for_errors("Building configuration...\nhostname R1\n")


def test_check_netmiko_output_for_errors_raises_on_invalid_input():
    """The exact line IOS XE emits when an `ip address` lands on a switchport."""
    output = (
        "interface GigabitEthernet0/1/3\n"
        " ip address 10.0.0.1 255.255.255.0\n"
        "% Invalid input detected at '^' marker.\n"
        " no shutdown\n"
    )
    with pytest.raises(wt.WriteRejectedError, match="Invalid input detected"):
        wt._check_netmiko_output_for_errors(output)


def test_set_interface_ip_raises_when_device_silently_rejects(_mock_pool, _mock_snapshot):
    """The 2026-05-18 bug: send_config_set returned cleanly but the device
    emitted '% Invalid input' for the `ip address` on a L2-only port. Tool
    must raise WriteRejectedError + mark the action FAILED instead of
    reporting success."""
    _mock_pool.send_config_set.return_value = (
        "interface GigabitEthernet0/1/3\n"
        " no switchport\n"
        "          ^\n"
        "% Invalid input detected at '^' marker.\n"
        " ip address 192.168.40.1 255.255.255.0\n"
        " no shutdown\n"
    )
    aid = propose_action("set_interface_ip", {})
    approve_action(aid)

    with pytest.raises(wt.WriteRejectedError, match="Invalid input"):
        wt.set_interface_ip("Gi0/1/3", "192.168.40.1", "255.255.255.0", action_id=aid)

    assert get_action(aid)["state"] == "FAILED"
    # Forensic post-snapshot was still taken so the operator can diff.
    snap_phases = [c.args[1] for c in _mock_snapshot.call_args_list]
    assert "pre" in snap_phases and "post" in snap_phases


def test_set_hostname_raises_when_running_config_missing_new_hostname(_mock_pool, _mock_snapshot):
    """send_config_set clean, no '%' errors, but the show-back doesn't
    contain the new hostname → verify miss → WriteRejectedError + FAILED."""
    _mock_pool.send_config_set.return_value = "config applied"
    _mock_pool.send_command.return_value = "hostname OLD-NAME\n"  # new name not present

    aid = propose_action("set_hostname", {"name": "BRAND-NEW"})
    approve_action(aid)

    with pytest.raises(wt.WriteRejectedError, match="post-write verify missed"):
        wt.set_hostname("BRAND-NEW", action_id=aid)

    assert get_action(aid)["state"] == "FAILED"


def test_set_access_vlan_raises_when_vlan_brief_missing_new_row(_mock_pool, _mock_snapshot):
    """`show vlan brief` doesn't list the new VLAN → verify miss → FAILED."""
    _mock_pool.send_config_set.return_value = "config applied"
    _mock_pool.send_command.return_value = (
        "VLAN Name                             Status    Ports\n"
        "1    default                          active    Gi0/1/0\n"
        # VLAN 99 was supposed to be created but is missing from the output
    )

    aid = propose_action("set_access_vlan", {"vlan_id": 99, "vlan_name": "ABSENT"})
    approve_action(aid)

    with pytest.raises(wt.WriteRejectedError, match="post-write verify missed"):
        wt.set_access_vlan(99, "ABSENT", action_id=aid)

    assert get_action(aid)["state"] == "FAILED"


# ---------------------------------------------------------------------------
# CLI AI configure — validators
# ---------------------------------------------------------------------------


def test_validate_config_commands_accepts_safe_block():
    wt._validate_config_commands(
        [
            "router ospf 100",
            "network 10.0.0.0 0.255.255.255 area 0",
            "exit",
            "interface Vlan1",
            "ip ospf 100 area 0",
            "exit",
        ]
    )


@pytest.mark.parametrize(
    "bad_cmd,fragment",
    [
        ("reload", "reload reboots"),
        ("reload in 5", "reload reboots"),
        ("erase startup-config", "erase wipes"),
        ("delete flash:running-backup", "delete removes"),
        ("format flash:", "format wipes"),
        ("write erase", "write erase"),
        ("boot system flash:c1100-universalk9.bin", "boot system"),
        ("enable secret 0 hunter2", "privileged access"),
        ("enable password cisco", "privileged access"),
        ("username badguy privilege 15 password x", "privileged access"),
        ("router ospf 100\nreload", "newline or semicolon"),
        ("router ospf 100; reload", "newline or semicolon"),
    ],
)
def test_validate_config_commands_rejects_unsafe(bad_cmd, fragment):
    with pytest.raises(ValueError, match=fragment):
        wt._validate_config_commands([bad_cmd])


def test_validate_config_commands_rejects_empty_list():
    with pytest.raises(ValueError, match="non-empty list"):
        wt._validate_config_commands([])


def test_validate_config_commands_rejects_non_list():
    with pytest.raises(ValueError, match="non-empty list"):
        wt._validate_config_commands("router ospf 100")  # type: ignore[arg-type]


def test_validate_config_commands_rejects_non_string_entry():
    with pytest.raises(ValueError, match="non-empty string"):
        wt._validate_config_commands([42])  # type: ignore[list-item]


def test_validate_verify_command_accepts_show():
    wt._validate_verify_command("show ip ospf")
    wt._validate_verify_command("show ip ospf | include 100")
    wt._validate_verify_command("SHOW running-config | section router ospf")


@pytest.mark.parametrize(
    "bad_cmd,fragment",
    [
        ("reload", "must start with 'show '"),
        ("ping 8.8.8.8", "must start with 'show '"),
        ("show ip ospf\nreload", "newline or semicolon"),
        ("show ip ospf; reload", "newline or semicolon"),
        ("", "non-empty string"),
    ],
)
def test_validate_verify_command_rejects_unsafe(bad_cmd, fragment):
    with pytest.raises(ValueError, match=fragment):
        wt._validate_verify_command(bad_cmd)


def test_validate_verify_pattern_accepts_compilable():
    wt._validate_verify_pattern(r"Routing Process \"ospf 100\"")
    wt._validate_verify_pattern(r"^\s*ip ospf 100 area 0$")


def test_validate_verify_pattern_rejects_bad_regex():
    with pytest.raises(ValueError, match="not a valid regex"):
        wt._validate_verify_pattern("(unclosed")


def test_validate_verify_pattern_rejects_empty():
    with pytest.raises(ValueError, match="non-empty string"):
        wt._validate_verify_pattern("")


# ---------------------------------------------------------------------------
# CLI AI configure — cli_configure executor
# ---------------------------------------------------------------------------


def _propose_and_approve_cli(action_params: dict) -> str:
    aid = propose_action("cli_configure", action_params)
    approve_action(aid)
    return aid


def test_cli_configure_happy_path(_mock_pool, _mock_snapshot):
    """Approved action → send_config_set runs → verify matches → mark_executed."""
    _mock_pool.send_config_set.return_value = "OSPF 100 enabled"
    _mock_pool.send_command.return_value = (
        'Routing Process "ospf 100" with ID 192.168.10.1\nDomain ID 0.0.0.0 (0x00000000)'
    )

    aid = _propose_and_approve_cli(
        {
            "config_commands": ["router ospf 100", "exit"],
            "verify_command": "show ip ospf",
            "verify_pattern": r'Routing Process "ospf 100"',
        }
    )

    result = wt.cli_configure(
        action_id=aid,
        config_commands=["router ospf 100", "exit"],
        verify_command="show ip ospf",
        verify_pattern=r'Routing Process "ospf 100"',
    )

    assert result["tool"] == "cli_configure"
    assert result["verify_matched"] is True
    assert result["params"]["command_count"] == 2
    assert "Routing Process" in result["verify_output_preview"]
    _mock_pool.send_config_set.assert_called_once()
    _mock_pool.send_command.assert_called_once_with("show ip ospf", read_timeout=60)


def test_cli_configure_verify_miss_marks_failed(_mock_pool, _mock_snapshot):
    """Regex misses → mark_failed + verify_failed error + post-snapshot taken."""
    _mock_pool.send_config_set.return_value = "applied"
    _mock_pool.send_command.return_value = "no OSPF running on this device"

    aid = _propose_and_approve_cli(
        {
            "config_commands": ["router ospf 100"],
            "verify_command": "show ip ospf",
            "verify_pattern": r'Routing Process "ospf 100"',
        }
    )

    result = wt.cli_configure(
        action_id=aid,
        config_commands=["router ospf 100"],
        verify_command="show ip ospf",
        verify_pattern=r'Routing Process "ospf 100"',
    )

    assert result["error"] == "verify_failed"
    assert "no OSPF" in result["verify_output_preview"]
    # Both pre and post snapshots fired (so diff is preserved)
    snap_phases = [c.args[1] for c in _mock_snapshot.call_args_list]
    assert "pre" in snap_phases and "post" in snap_phases


def test_cli_configure_verify_miss_extracts_device_errors(_mock_pool, _mock_snapshot):
    """When verify misses AND config_output has IOS XE '%' error lines
    (e.g. duplicate router-id), surface them as device_errors so the
    operator sees WHY verify failed. OSPF process N with router-id
    already in use is the canonical case Filip hit on 2026-05-15."""
    _mock_pool.send_config_set.return_value = (
        "router ospf 5\nrouter-id 10.0.0.1\n% Router-ID 10.0.0.1 in use by ospf process 2\nexit\n"
    )
    _mock_pool.send_command.return_value = (
        'Routing Process "ospf 2" with ID 10.0.0.1\n'
        'Routing Process "ospf 100" with ID 192.168.10.1\n'
    )

    aid = _propose_and_approve_cli(
        {
            "config_commands": ["router ospf 5", "router-id 10.0.0.1", "exit"],
            "verify_command": "show ip ospf | include Routing Process",
            "verify_pattern": r'Routing Process "ospf 5"',
        }
    )

    result = wt.cli_configure(
        action_id=aid,
        config_commands=["router ospf 5", "router-id 10.0.0.1", "exit"],
        verify_command="show ip ospf | include Routing Process",
        verify_pattern=r'Routing Process "ospf 5"',
    )

    assert result["error"] == "verify_failed"
    assert result["device_errors"], "device_errors must surface % lines from config_output"
    assert any("Router-ID 10.0.0.1 in use" in err for err in result["device_errors"])


def test_cli_configure_verify_miss_empty_device_errors_when_clean(_mock_pool, _mock_snapshot):
    """If config_output has no '%' lines, device_errors is an empty list
    (not None / not missing). Stable contract for downstream callers."""
    _mock_pool.send_config_set.return_value = "router ospf 7\nrouter-id 10.0.0.7\nexit\n"
    _mock_pool.send_command.return_value = "completely unrelated output"

    aid = _propose_and_approve_cli(
        {
            "config_commands": ["router ospf 7"],
            "verify_command": "show ip ospf",
            "verify_pattern": "nope",
        }
    )

    result = wt.cli_configure(
        action_id=aid,
        config_commands=["router ospf 7"],
        verify_command="show ip ospf",
        verify_pattern="nope",
    )

    assert result["error"] == "verify_failed"
    assert result["device_errors"] == []


def test_cli_configure_rejects_unsafe_at_execute_time(_mock_pool, _mock_snapshot):
    """Tampered action dict containing 'reload' → validator raises before
    any Netmiko call. Defense-in-depth: even if propose was skipped or
    the params were rewritten between approve and execute."""
    aid = _propose_and_approve_cli({})  # params don't matter — validator runs on the args

    with pytest.raises(ValueError, match="reload reboots"):
        wt.cli_configure(
            action_id=aid,
            config_commands=["router ospf 100", "reload"],
            verify_command="show ip ospf",
            verify_pattern=r"x",
        )
    _mock_pool.send_config_set.assert_not_called()


def test_cli_configure_refuses_without_approval(_mock_pool, _mock_snapshot):
    aid = propose_action("cli_configure", {})  # NOT approved
    with pytest.raises(NotApproved):
        wt.cli_configure(
            action_id=aid,
            config_commands=["router ospf 100"],
            verify_command="show ip ospf",
            verify_pattern=r"ospf",
        )
    _mock_pool.send_config_set.assert_not_called()


def test_cli_configure_send_config_failure_marks_failed(_mock_pool, _mock_snapshot):
    """Netmiko raises during send_config_set → mark_failed, exception
    propagates (CLAUDE.md §76: 'never auto-retry')."""
    _mock_pool.send_config_set.side_effect = RuntimeError("SSH timeout")

    aid = _propose_and_approve_cli({})

    with pytest.raises(RuntimeError, match="SSH timeout"):
        wt.cli_configure(
            action_id=aid,
            config_commands=["router ospf 100"],
            verify_command="show ip ospf",
            verify_pattern=r"ospf",
        )


def test_cli_configure_verify_ssh_failure_returns_structured(_mock_pool, _mock_snapshot):
    """Netmiko raises during verify send_command → action FAILED but the
    write itself succeeded. Return verify_ssh_failed with both snapshot
    paths so Filip can compare."""
    _mock_pool.send_config_set.return_value = "applied"
    _mock_pool.send_command.side_effect = RuntimeError("read timeout")

    aid = _propose_and_approve_cli({})

    result = wt.cli_configure(
        action_id=aid,
        config_commands=["router ospf 100"],
        verify_command="show ip ospf",
        verify_pattern=r"ospf",
    )

    assert result["error"] == "verify_ssh_failed"
    assert "read timeout" in result["message"]
    assert "snapshot_pre" in result and "snapshot_post" in result
