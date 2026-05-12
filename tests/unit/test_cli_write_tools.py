"""Unit tests for cli_agent.write_tools — mocked SSH, no real device."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

import backend.cli_agent.write_tools as wt
from backend.orchestration.confirmations import (
    NotApproved,
    _reset_for_testing,
    approve_action,
    propose_action,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_actions():
    _reset_for_testing()
    yield
    _reset_for_testing()


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


def test_set_hostname_never_touches_device_when_not_approved(
    _mock_pool, _mock_snapshot
):
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

    assert call_order[0] == "snapshot"   # pre fires first
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
    action_id = propose_action("set_interface_ip", {})
    approve_action(action_id)
    wt.set_interface_ip("GigabitEthernet0/0/0", "10.1.1.1", "255.255.255.0", action_id=action_id)
    cmd_list = _mock_pool.send_config_set.call_args.args[0]
    joined = " ".join(cmd_list)
    assert "GigabitEthernet0/0/0" in joined
    assert "10.1.1.1" in joined
    assert "255.255.255.0" in joined
    assert "no shutdown" in joined
