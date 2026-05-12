"""Netmiko connection pool — one persistent SSH session per device.

Rules (from PROJECT_PLAN.md):
- Retry only on initial connect, never on a command send.
- Auth failures are never retried — surface immediately.
- If the host key hasn't been accepted yet, give a clear actionable message.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from netmiko import ConnectHandler
from netmiko.exceptions import NetMikoAuthenticationException

from backend.core.logging import get_logger

log = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY_S = 2.0


class ConnectionPool:
    def __init__(self) -> None:
        self._pool: dict[tuple[str, str], Any] = {}

    def get_connection(
        self,
        host: str,
        user: str,
        password: str,
        device_type: str = "cisco_ios",
    ) -> Any:
        """Return a live connection from the pool, creating one if necessary.

        Raises:
            RuntimeError: SSH host key not in known_hosts — caller must pre-accept.
            NetMikoAuthenticationException: wrong credentials, never retried.
            ConnectionError: could not reach the device after _MAX_RETRIES attempts.
        """
        key = (host, user)
        conn = self._pool.get(key)
        if conn is not None:
            alive = False
            try:
                alive = bool(conn.is_alive())
            except Exception as exc:
                # is_alive() itself blew up — treat as dead and rebuild.
                # Don't leak the corpse into a retry. Audit #8.
                log.info("connection_is_alive_failed", host=host, user=user, error=str(exc))
            if alive:
                return conn
            log.info("connection_stale", host=host, user=user)
            self._pool.pop(key, None)
            # Best-effort close on the corpse so we don't leak the SSH socket
            with contextlib.suppress(Exception):
                conn.disconnect()

        conn = self._connect(host, user, password, device_type)
        self._pool[key] = conn
        log.info("connection_created", host=host, user=user, device_type=device_type)
        return conn

    def _connect(
        self,
        host: str,
        user: str,
        password: str,
        device_type: str,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return ConnectHandler(
                    device_type=device_type,
                    host=host,
                    username=user,
                    password=password,
                    conn_timeout=10,
                )
            except NetMikoAuthenticationException:
                raise  # wrong creds — never retry
            except Exception as exc:
                msg = str(exc).lower()
                if "not found in known_hosts" in msg or "server key mismatch" in msg:
                    raise RuntimeError(
                        f"SSH host key for {host!r} has not been accepted. "
                        f"Run: ssh -o StrictHostKeyChecking=accept-new {user}@{host} exit"
                    ) from exc
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    log.warning(
                        "connection_retry",
                        host=host,
                        attempt=attempt,
                        error=str(exc),
                    )
                    time.sleep(_RETRY_DELAY_S)

        raise ConnectionError(
            f"Could not connect to {host!r} after {_MAX_RETRIES} attempts"
        ) from last_exc

    def invalidate(self, host: str, user: str) -> None:
        """Remove a connection from the pool and disconnect it.

        Call this after any operation that changes device state in a way that
        affects the SSH session — specifically after a hostname change, which
        alters the router prompt and makes the cached base_prompt stale.
        """
        conn = self._pool.pop((host, user), None)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.disconnect()
            log.info("connection_invalidated", host=host, user=user)

    def close_all(self) -> None:
        for conn in list(self._pool.values()):
            with contextlib.suppress(Exception):
                conn.disconnect()
        self._pool.clear()
        log.info("connection_pool_closed")


# Module-level singleton used by read_tools / write_tools.
pool = ConnectionPool()
