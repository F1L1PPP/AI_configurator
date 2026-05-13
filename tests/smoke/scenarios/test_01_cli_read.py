"""§2 Scenario 1 — CLI read: show interfaces + show version."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


def test_show_ip_interface_brief_returns_rows(router_reachable):
    from backend.cli_agent.read_tools import show_ip_interface_brief

    result = show_ip_interface_brief()
    # Result is normalized to a list of interface dicts (or raw string fallback)
    assert result is not None
    if isinstance(result, list):
        assert len(result) > 0
        # At minimum the C1111 should show GigabitEthernet0/0/0
        names = {row.get("interface", "") for row in result}
        assert any("GigabitEthernet" in n for n in names), names


def test_show_version_returns_dict(router_reachable):
    from backend.cli_agent.read_tools import show_version

    result = show_version()
    assert result is not None
    # ntc-templates returns a list of dicts; we expect at least one
    assert isinstance(result, list | dict)
