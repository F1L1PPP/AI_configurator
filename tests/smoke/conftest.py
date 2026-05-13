"""Shared fixtures for the smoke harness.

Smoke tests hit the real router (and, for the WebUI scenarios, launch a
real headed Chromium). They auto-skip when prerequisites aren't met so
the same suite runs cleanly in CI (where the router isn't reachable),
on a dev laptop (where it usually is), and against a live cabled setup
(where SMOKE_ALLOW_WRITES=1 unlocks the write paths).

Skip matrix:
- `router_reachable`  — skip if SSH to ROUTER_HOST refuses TCP connect
- `writes_allowed`    — skip if SMOKE_ALLOW_WRITES != "1"
- `webui_enabled`     — skip if ROUTER_WEBUI_BASE_URL isn't set
"""

from __future__ import annotations

import os
import socket

import pytest

from backend.core.settings import get_settings


@pytest.fixture(autouse=True, scope="session")
def _smoke_results_dir(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Set up artifacts/smoke/<timestamp>/ for evidence; tests can ignore."""
    return None


@pytest.fixture()
def router_reachable() -> None:
    """Skip the test unless ROUTER_HOST accepts a TCP connection on port 22."""
    settings = get_settings()
    host = settings.router_host
    if not host:
        pytest.skip("ROUTER_HOST not set in .env — skipping router smoke scenario")
    try:
        with socket.create_connection((host, 22), timeout=3.0):
            return
    except OSError as exc:
        pytest.skip(f"router {host}:22 unreachable ({exc}) — skipping smoke scenario")


@pytest.fixture()
def writes_allowed() -> None:
    """Skip write-path smoke scenarios unless explicitly opted-in.

    Set SMOKE_ALLOW_WRITES=1 in the environment to allow hostname / VLAN
    write scenarios to actually mutate the router. CI never sets this;
    a developer running against a known-safe lab box does.
    """
    if os.environ.get("SMOKE_ALLOW_WRITES") != "1":
        pytest.skip(
            "Write scenarios disabled. Set SMOKE_ALLOW_WRITES=1 to enable "
            "(only against a router you own and don't mind mutating)."
        )


@pytest.fixture()
def webui_enabled() -> None:
    """Skip WebUI scenarios unless ROUTER_WEBUI_BASE_URL is configured."""
    settings = get_settings()
    if not settings.router_webui_base_url:
        pytest.skip("ROUTER_WEBUI_BASE_URL not set in .env — skipping WebUI smoke scenario")
