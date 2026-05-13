"""§2 Scenario 6 — WebUI write: add access VLAN via Playwright (Day 7 centrepiece)."""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.webui, pytest.mark.slow]


def test_webui_add_access_vlan_round_trip(router_reachable, webui_enabled, writes_allowed):
    """Drive the WebUI to add VLAN, verify via CLI 'show vlan brief', clean up.

    Uses VLAN 999 to avoid colliding with any production VLAN. Always
    runs `no vlan 999` afterwards so the router doesn't accumulate
    smoke-test VLANs.
    """
    from backend.cli_agent.connection import pool
    from backend.cli_agent.read_tools import show_vlan_brief
    from backend.core.settings import get_settings
    from backend.orchestration.confirmations import approve_action, propose_action
    from backend.webui_agent.flows.add_access_vlan import add_access_vlan_via_webui

    headless = os.environ.get("SMOKE_HEADLESS") == "1"
    s = get_settings()
    vlan_id = 999
    vlan_name = "SMOKE-OFFICE"

    # Belt-and-suspenders: if a previous failed run left VLAN 999 behind, remove it.
    def _cleanup_vlan() -> None:
        try:
            conn = pool.get_connection(s.router_host, s.router_ssh_user, s.router_ssh_password)
            conn.send_config_set([f"no vlan {vlan_id}"], read_timeout=15)
            pool.invalidate(s.router_host, s.router_ssh_user)
        except Exception:
            pass  # cleanup is best-effort

    _cleanup_vlan()

    try:
        aid = propose_action("webui_add_access_vlan", {"vlan_id": vlan_id, "vlan_name": vlan_name})
        approve_action(aid)
        result = add_access_vlan_via_webui(vlan_id, vlan_name, action_id=aid, headless=headless)
        assert result["tool"] == "webui_add_access_vlan"
        assert result["vlan_id"] == vlan_id
        assert result["verified"] is True

        # Independent CLI verify
        pool.invalidate(s.router_host, s.router_ssh_user)
        rows = show_vlan_brief()
        ids = {row.get("vlan_id") for row in rows}
        assert str(vlan_id) in ids, (
            f"VLAN {vlan_id} not in `show vlan brief` after WebUI save: {ids}"
        )
    finally:
        _cleanup_vlan()
