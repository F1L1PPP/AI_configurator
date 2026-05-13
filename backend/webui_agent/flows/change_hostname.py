"""End-to-end WebUI hostname change flow.

Composition of Day 4 building blocks:

    browser → login → HostnamePage → Apply → CLI verify

Safety:
- Requires an APPROVED action_id (defense-in-depth: planner registry layer 1
  + this function's `_guard` layer 2).
- Pre-snapshot before any UI interaction; post-snapshot after success.
- Every step screenshotted into artifacts/screenshots/<flow>_<action_id>/.
- On error: DOM dump + mark action FAILED + re-raise. Never auto-retry.
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
from backend.webui_agent.pages.hostname_page import HostnamePage
from backend.webui_agent.verify import verify_hostname

log = get_logger(__name__)


class WebUIVerificationError(RuntimeError):
    """Raised when the WebUI clicked but CLI verification disagrees."""


def _guard(action_id: str) -> None:
    if not is_approved(action_id):
        raise NotApproved(
            f"action_id {action_id!r} has not been approved. "
            "Call POST /api/approve/{action_id} first."
        )


def change_hostname_via_webui(
    new_name: str,
    action_id: str,
    *,
    headless: bool | None = None,
) -> dict:
    """Drive the WebUI to rename the router. Returns a structured result.

    Args:
        new_name:  New hostname to set (e.g. "LAB-R1").
        action_id: Must be in state APPROVED.
        headless:  Defaults to None → `webui_browser._resolve_headless`
                   reads PLAYWRIGHT_HEADLESS / CI env vars, falling back
                   to False (dev / watch-it-click).

    Returns:
        dict with keys: tool, old_hostname, new_hostname, snapshot_pre,
        snapshot_post, screenshots, verified.

    Raises:
        NotApproved:            action not in APPROVED state
        WebUIVerificationError: WebUI clicked but CLI doesn't see the change
        Any Playwright exception bubbles up unchanged
    """
    _guard(action_id)

    log.info("change_hostname_via_webui_start", new_name=new_name, action_id=action_id)
    ev = EvidenceCollector("change_hostname", action_id=action_id)

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

            hp = HostnamePage(page)
            hp.goto()
            ev.step("03-hostname-form", page)

            old_hostname = hp.get_current_hostname()
            log.info("change_hostname_read_old", old=old_hostname)

            hp.set_hostname(new_name)
            ev.step("04-form-filled", page)

            hp.apply()
            ev.step("05-applied", page)

        # The router's prompt has changed; invalidate the pooled SSH so the
        # next CLI call reconnects and discovers the new prompt (same fix
        # we did for set_hostname in cli_agent/write_tools.py).
        s = get_settings()
        pool.invalidate(s.router_host, s.router_ssh_user)

        # Verify via CLI — ground truth
        verified = verify_hostname(new_name)
        if not verified:
            raise WebUIVerificationError(
                f"WebUI clicked Apply but CLI does not see 'hostname {new_name}' "
                "in running-config. Screenshots saved; investigate."
            )

        # Post-snapshot
        post_dir = take_snapshot(action_id, "post")
        mark_executed(action_id)

        log.info(
            "change_hostname_via_webui_complete",
            old=old_hostname,
            new=new_name,
            screenshots=str(ev.session_dir),
        )

        return {
            "tool": "webui_set_hostname",
            "old_hostname": old_hostname,
            "new_hostname": new_name,
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
            "change_hostname_via_webui_failed",
            action_id=action_id,
            error=str(exc),
        )
        raise
