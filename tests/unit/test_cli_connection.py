"""Unit tests for cli_agent.connection — mocked Netmiko, no real SSH."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from netmiko.exceptions import NetMikoAuthenticationException, NetMikoTimeoutException

from backend.cli_agent.connection import _MAX_RETRIES, _RETRY_DELAY_S, ConnectionPool


@pytest.fixture()
def pool() -> ConnectionPool:
    return ConnectionPool()


def _mock_conn(alive: bool = True) -> MagicMock:
    conn = MagicMock()
    conn.is_alive.return_value = alive
    return conn


# ---------------------------------------------------------------------------
# Pool reuse
# ---------------------------------------------------------------------------


def test_existing_live_connection_is_reused(pool: ConnectionPool) -> None:
    conn = _mock_conn(alive=True)
    pool._pool[("10.0.0.1", "admin")] = conn

    with patch("backend.cli_agent.connection.ConnectHandler") as mock_ch:
        result = pool.get_connection("10.0.0.1", "admin", "pass")

    assert result is conn
    mock_ch.assert_not_called()


def test_stale_connection_is_replaced(pool: ConnectionPool) -> None:
    old_conn = _mock_conn(alive=False)
    pool._pool[("10.0.0.1", "admin")] = old_conn
    new_conn = _mock_conn(alive=True)

    with patch("backend.cli_agent.connection.ConnectHandler", return_value=new_conn):
        result = pool.get_connection("10.0.0.1", "admin", "pass")

    assert result is new_conn
    assert pool._pool[("10.0.0.1", "admin")] is new_conn


# ---------------------------------------------------------------------------
# Connect-retry (only on connect, not on send_command)
# ---------------------------------------------------------------------------


def test_connect_retries_on_timeout(pool: ConnectionPool) -> None:
    good_conn = _mock_conn()
    side_effects = [NetMikoTimeoutException("timeout"), good_conn]

    with (
        patch("backend.cli_agent.connection.ConnectHandler", side_effect=side_effects),
        patch("backend.cli_agent.connection.time.sleep") as mock_sleep,
    ):
        result = pool.get_connection("10.0.0.1", "admin", "pass")

    assert result is good_conn
    mock_sleep.assert_called_once_with(_RETRY_DELAY_S)


def test_connect_exhausts_retries_and_raises(pool: ConnectionPool) -> None:
    with (
        patch(
            "backend.cli_agent.connection.ConnectHandler",
            side_effect=NetMikoTimeoutException("timeout"),
        ),
        patch("backend.cli_agent.connection.time.sleep"),
        pytest.raises(ConnectionError),
    ):
        pool.get_connection("10.0.0.1", "admin", "pass")


def test_connect_retry_count_matches_max(pool: ConnectionPool) -> None:
    with (
        patch(
            "backend.cli_agent.connection.ConnectHandler",
            side_effect=NetMikoTimeoutException("timeout"),
        ) as mock_ch,
        patch("backend.cli_agent.connection.time.sleep"),
        pytest.raises(ConnectionError),
    ):
        pool.get_connection("10.0.0.1", "admin", "pass")

    assert mock_ch.call_count == _MAX_RETRIES


# ---------------------------------------------------------------------------
# Auth failure — never retried
# ---------------------------------------------------------------------------


def test_auth_failure_is_not_retried(pool: ConnectionPool) -> None:
    with (
        patch(
            "backend.cli_agent.connection.ConnectHandler",
            side_effect=NetMikoAuthenticationException("bad creds"),
        ) as mock_ch,
        pytest.raises(NetMikoAuthenticationException),
    ):
        pool.get_connection("10.0.0.1", "admin", "wrong")

    assert mock_ch.call_count == 1  # exactly once, no retry


# ---------------------------------------------------------------------------
# Host-key error surfaces a helpful RuntimeError
# ---------------------------------------------------------------------------


def test_unknown_host_key_raises_runtime_error(pool: ConnectionPool) -> None:
    with (
        patch(
            "backend.cli_agent.connection.ConnectHandler",
            side_effect=Exception("not found in known_hosts"),
        ),
        pytest.raises(RuntimeError, match="StrictHostKeyChecking"),
    ):
        pool.get_connection("10.0.0.1", "admin", "pass")


# ---------------------------------------------------------------------------
# close_all
# ---------------------------------------------------------------------------


def test_close_all_disconnects_and_clears(pool: ConnectionPool) -> None:
    c1, c2 = _mock_conn(), _mock_conn()
    pool._pool[("h1", "u")] = c1
    pool._pool[("h2", "u")] = c2

    pool.close_all()

    c1.disconnect.assert_called_once()
    c2.disconnect.assert_called_once()
    assert len(pool._pool) == 0
