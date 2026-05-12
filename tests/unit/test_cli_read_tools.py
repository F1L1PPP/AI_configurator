"""Unit tests for cli_agent.read_tools — mocked connection, no SSH needed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import backend.cli_agent.read_tools as rt

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the module-level pool with a mock so no real SSH is attempted."""
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.get_connection.return_value = mock_conn
    monkeypatch.setattr(rt, "pool", mock_pool)
    return mock_conn


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    fake.router_host = "10.0.0.1"
    fake.router_ssh_user = "admin"
    fake.router_ssh_password = "pass"
    monkeypatch.setattr(rt, "get_settings", lambda: fake)


# ---------------------------------------------------------------------------
# show_version
# ---------------------------------------------------------------------------


def test_show_version_returns_dict(_mock_pool: MagicMock) -> None:
    _mock_pool.send_command.return_value = "IOS XE version output"
    with patch("backend.cli_agent.read_tools.parse", return_value=[{"version": "17.6"}]):
        result = rt.show_version()
    assert isinstance(result, dict)
    assert result.get("version") == "17.6"


def test_show_version_fallback_when_parse_returns_string(_mock_pool: MagicMock) -> None:
    _mock_pool.send_command.return_value = "raw version text"
    with patch("backend.cli_agent.read_tools.parse", return_value="raw version text"):
        result = rt.show_version()
    assert isinstance(result, dict)
    assert "raw" in result


# ---------------------------------------------------------------------------
# show_ip_interface_brief
# ---------------------------------------------------------------------------


def test_show_ip_interface_brief_returns_list(_mock_pool: MagicMock) -> None:
    _mock_pool.send_command.return_value = ""
    rows = [{"intf": "Gi0/0/0", "ipaddr": "192.168.10.1"}]
    with patch("backend.cli_agent.read_tools.parse", return_value=rows):
        result = rt.show_ip_interface_brief()
    assert isinstance(result, list)
    assert result == rows


def test_show_ip_interface_brief_returns_empty_list_on_parse_fail(
    _mock_pool: MagicMock,
) -> None:
    _mock_pool.send_command.return_value = "output"
    with patch("backend.cli_agent.read_tools.parse", return_value="output"):
        result = rt.show_ip_interface_brief()
    assert result == []


# ---------------------------------------------------------------------------
# show_running_config
# ---------------------------------------------------------------------------


def test_show_running_config_returns_string(_mock_pool: MagicMock) -> None:
    _mock_pool.send_command.return_value = "hostname LAB-R1\n!"
    result = rt.show_running_config()
    assert isinstance(result, str)
    assert "hostname" in result


def test_show_running_config_does_not_call_parse(_mock_pool: MagicMock) -> None:
    _mock_pool.send_command.return_value = "cfg"
    with patch("backend.cli_agent.read_tools.parse") as mock_parse:
        rt.show_running_config()
    mock_parse.assert_not_called()


# ---------------------------------------------------------------------------
# show_vlan_brief
# ---------------------------------------------------------------------------


def test_show_vlan_brief_returns_list(_mock_pool: MagicMock) -> None:
    _mock_pool.send_command.return_value = ""
    rows = [{"vlan_id": "1", "name": "default", "status": "active"}]
    with patch("backend.cli_agent.read_tools.parse", return_value=rows):
        result = rt.show_vlan_brief()
    assert isinstance(result, list)
    assert result[0]["vlan_id"] == "1"


# ---------------------------------------------------------------------------
# Logging side-effect: every tool call writes a JSONL line
# ---------------------------------------------------------------------------


def test_show_version_logs_tool_call(_mock_pool: MagicMock) -> None:
    _mock_pool.send_command.return_value = ""
    with (
        patch("backend.cli_agent.read_tools.parse", return_value=[{"v": "1"}]),
        patch.object(rt.log, "info") as mock_log,
    ):
        rt.show_version()
    mock_log.assert_called_once()
    call_kwargs = mock_log.call_args
    assert call_kwargs.args[0] == "tool_call"
    assert call_kwargs.kwargs.get("tool") == "show_version"
    assert "duration_ms" in call_kwargs.kwargs
