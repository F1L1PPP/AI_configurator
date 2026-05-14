"""End-to-end WebUI add-access-VLAN flow.

Composition of the same building blocks as `change_hostname.py`:

    browser → login → VlanPage.goto → click_add → fill → save → CLI verify

Safety:
- Requires an APPROVED action_id (defense-in-depth: dispatcher layer 1
  + this function's `_guard` layer 2).
- Pre-snapshot before any UI interaction; post-snapshot after success.
- Every step screenshotted into artifacts/screenshots/<flow>_<action_id>/.
- On error: DOM dump + mark action FAILED + re-raise. Never auto-retry.
- CLI ground-truth check after save: `verify_vlan_exists(vlan_id, name)`
  reads `show vlan brief` and confirms the row landed in the VLAN database.
"""

from __future__ import annotations

import contextlib

from backend.cli_agent.connection import pool
from backend.cli_agent.snapshots import take_snapshot
from backend.core.logging import get_logger
from backend.core.settings import get_settings
from backend.orchestration.confirmations import (
    NotApproved,
    is_approved,
    mark_executed,
    mark_failed,
)
from backend.webui_agent.browser import webui_browser
from backend.webui_agent.evidence import EvidenceCollector
from backend.webui_agent.login import login
from backend.webui_agent.pages.vlan_page import VlanPage
from backend.webui_agent.verify import verify_vlan_exists

log = get_logger(__name__)


class WebUIVerificationError(RuntimeError):
    """Raised when the WebUI clicked Save but CLI doesn't see the VLAN."""


def _guard(action_id: str) -> None:
    if not is_approved(action_id):
        raise NotApproved(
            f"action_id {action_id!r} has not been approved. "
            "Call POST /api/approve/{action_id} first."
        )


def add_access_vlan_via_webui(
    vlan_id: int,
    vlan_name: str,
    action_id: str,
    *,
    headless: bool | None = None,
) -> dict:
    """Drive the WebUI to add an access VLAN. Returns a structured result.

    Args:
        vlan_id:   VLAN number (1..4094). Router validates the range.
        vlan_name: Human-readable name (e.g. "OFFICE").
        action_id: Must be in state APPROVED.
        headless:  Defaults to None → `webui_browser._resolve_headless`
                   reads PLAYWRIGHT_HEADLESS or CI env vars, falling back
                   to False (dev / watch-it-click). Pass True/False
                   explicitly to override.

    Returns:
        dict with keys: tool, vlan_id, vlan_name, snapshot_pre,
        snapshot_post, screenshots, verified.

    Raises:
        NotApproved:            action not in APPROVED state
        WebUIVerificationError: WebUI clicked Save but CLI doesn't see the VLAN
        Any Playwright exception bubbles up unchanged
    """
    _guard(action_id)

    log.info(
        "add_access_vlan_via_webui_start",
        vlan_id=vlan_id,
        vlan_name=vlan_name,
        action_id=action_id,
    )
    ev = EvidenceCollector("add_access_vlan", action_id=action_id)

    # Pre-snapshot via SSH (independent of the WebUI session)
    pre_dir = take_snapshot(action_id, "pre")

    page = None
    try:
        with webui_browser(headless=headless) as page:
            ev.step("01-browser-launched", page)

            if not login(page):
                ev.dump_dom(page, "99-login-failed")
                raise RuntimeError("WebUI login failed")
            ev.step("02-logged-in", page)

            vp = VlanPage(page)
            vp.goto()
            ev.step("03-vlan-page", page)

            vp.click_add()
            ev.step("04-add-form-opened", page)

            vp.set_vlan_id(vlan_id)
            vp.set_vlan_name(vlan_name)
            ev.step("05-form-filled", page)

            vp.save()
            ev.step("06-saved", page)

        # The VLAN database changed; invalidate the pooled SSH so the
        # next CLI call sees a fresh state (same pattern as hostname).
        s = get_settings()
        pool.invalidate(s.router_host, s.router_ssh_user)

        # Verify via CLI — ground truth
        verified = verify_vlan_exists(vlan_id, name=vlan_name)
        if not verified:
            raise WebUIVerificationError(
                f"WebUI clicked Save but `show vlan brief` does not list "
                f"VLAN {vlan_id} named {vlan_name!r}. Screenshots saved; investigate."
            )

        # Post-snapshot
        post_dir = take_snapshot(action_id, "post")
        mark_executed(action_id)

        log.info(
            "add_access_vlan_via_webui_complete",
            vlan_id=vlan_id,
            vlan_name=vlan_name,
            screenshots=str(ev.session_dir),
        )

        return {
            "tool": "webui_add_access_vlan",
            "vlan_id": vlan_id,
            "vlan_name": vlan_name,
            "snapshot_pre": str(pre_dir),
            "snapshot_post": str(post_dir),
            "screenshots": str(ev.session_dir),
            "verified": True,
        }

    except Exception as exc:
        if page is not None:
            with contextlib.suppress(Exception):
                ev.dump_dom(page, "99-error")
        mark_failed(action_id)
        log.error(
            "add_access_vlan_via_webui_failed",
            action_id=action_id,
            error=str(exc),
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        raise
