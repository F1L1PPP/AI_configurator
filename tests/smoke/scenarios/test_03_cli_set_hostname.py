"""§2 Scenario 3 — CLI write: change hostname (propose → approve → execute → verify → restore)."""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.smoke


def test_cli_hostname_round_trip(router_reachable, writes_allowed):
    """Full hostname round-trip: capture original, change, verify, restore.

    This is destructive in the sense that the hostname changes briefly,
    but the test always restores the original — so the router ends in
    the same state it started.
    """
    from backend.cli_agent import read_tools, write_tools
    from backend.cli_agent.connection import pool
    from backend.core.settings import get_settings
    from backend.orchestration.confirmations import approve_action, propose_action

    s = get_settings()

    # 1. Capture original hostname from show running-config
    cfg = read_tools.show_running_config()
    m = re.search(r"^\s*hostname\s+(\S+)\s*$", cfg, flags=re.MULTILINE)
    assert m is not None, "could not find current hostname in running-config"
    original = m.group(1)
    test_name = "SMOKE-CHECK"

    try:
        # 2. Propose + approve + execute the change
        aid = propose_action("set_hostname", {"name": test_name})
        approve_action(aid)
        result = write_tools.set_hostname(test_name, action_id=aid)
        assert result["tool"] == "set_hostname"

        # 3. The base_prompt is now stale — invalidate before verify
        pool.invalidate(s.router_host, s.router_ssh_user)

        # 4. Verify via independent CLI read
        cfg_after = read_tools.show_running_config()
        assert re.search(
            rf"^\s*hostname\s+{re.escape(test_name)}\s*$",
            cfg_after,
            flags=re.MULTILINE,
        ), f"hostname change did not land in running-config (looked for {test_name})"

    finally:
        # 5. Always restore the original hostname — even on assertion failure
        try:
            aid_restore = propose_action("set_hostname", {"name": original})
            approve_action(aid_restore)
            write_tools.set_hostname(original, action_id=aid_restore)
            pool.invalidate(s.router_host, s.router_ssh_user)
        except Exception as exc:
            pytest.fail(
                f"FAILED to restore original hostname {original!r}: {exc}. "
                "Router may be left with hostname SMOKE-CHECK — fix manually."
            )
