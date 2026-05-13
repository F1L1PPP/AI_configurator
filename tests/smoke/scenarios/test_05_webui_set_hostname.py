"""§2 Scenario 5 — WebUI write: change hostname via Playwright."""

from __future__ import annotations

import os
import re

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.webui, pytest.mark.slow]


def test_webui_hostname_round_trip(router_reachable, webui_enabled, writes_allowed):
    """Drive the WebUI to change hostname, verify via CLI, restore.

    Headed by default for visibility; set SMOKE_HEADLESS=1 to run headless.
    """
    from backend.cli_agent import read_tools
    from backend.cli_agent.connection import pool
    from backend.core.settings import get_settings
    from backend.orchestration.confirmations import approve_action, propose_action
    from backend.webui_agent.flows.change_hostname import change_hostname_via_webui

    headless = os.environ.get("SMOKE_HEADLESS") == "1"
    s = get_settings()

    cfg = read_tools.show_running_config()
    m = re.search(r"^\s*hostname\s+(\S+)\s*$", cfg, flags=re.MULTILINE)
    assert m is not None
    original = m.group(1)
    test_name = "SMOKE-WEBUI"

    try:
        aid = propose_action("webui_set_hostname", {"name": test_name})
        approve_action(aid)
        result = change_hostname_via_webui(test_name, action_id=aid, headless=headless)
        assert result["tool"] == "webui_set_hostname"
        assert result["new_hostname"] == test_name
        assert result["verified"] is True
    finally:
        # Restore via CLI (faster than reopening the browser)
        from backend.cli_agent import write_tools

        try:
            aid_r = propose_action("set_hostname", {"name": original})
            approve_action(aid_r)
            write_tools.set_hostname(original, action_id=aid_r)
            pool.invalidate(s.router_host, s.router_ssh_user)
        except Exception as exc:
            pytest.fail(f"FAILED to restore hostname {original!r}: {exc}")
