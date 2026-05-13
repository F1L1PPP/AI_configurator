"""§2 Scenario 2 — CLI read: show running-config."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


def test_show_running_config_returns_text(router_reachable):
    from backend.cli_agent.read_tools import show_running_config

    cfg = show_running_config()
    assert isinstance(cfg, str)
    assert len(cfg) > 100  # a real running-config is at least a few hundred chars
    # Sanity: must contain at least one of these baseline lines
    assert any(marker in cfg for marker in ("hostname ", "version ", "interface "))
