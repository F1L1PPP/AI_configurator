"""End-to-end WebUI add-access-VLAN flow.

Composition of the same building blocks as `change_hostname.py`:

    pre-snapshot → child(Playwright: browser → login → VlanPage → save)
                 → invalidate pool → CLI verify → post-snapshot

Why the Playwright portion runs in a child process:
    Windows + Playwright sync API + FastAPI thread pool deadlocks on
    asyncio's Catch-22 (Proactor lacks add_reader, Selector lacks
    subprocess_exec; Playwright needs both). Spawning a fresh Python
    process gives Playwright its own asyncio state, independent of
    FastAPI's. See `_playwright_subprocess.py` for the full rationale.

Safety:
- Requires an APPROVED action_id (defense-in-depth: dispatcher layer 1
  + this function's `_guard` layer 2).
- Pre-snapshot before any UI interaction; post-snapshot after success.
- Every step screenshotted into artifacts/screenshots/<flow>_<action_id>/
  (the child writes these; the parent doesn't touch them).
- On error: mark action FAILED + re-raise. Never auto-retry.
- CLI ground-truth check after save: `verify_vlan_exists(vlan_id, name)`
  reads `show vlan brief` and confirms the row landed in the VLAN database.
"""

from __future__ import annotations

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
from backend.webui_agent._subprocess import run_flow_in_subprocess
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

    The Playwright portion (browser launch → login → form → save → screenshots)
    runs in a child Python process to side-step the Windows asyncio
    Catch-22; everything else (guard, snapshots, verification, state
    transitions, pool invalidation) stays in this process.

    Args:
        vlan_id:   VLAN number (1..4094). Router validates the range.
        vlan_name: Human-readable name (e.g. "OFFICE").
        action_id: Must be in state APPROVED.
        headless:  None → child reads PLAYWRIGHT_HEADLESS / CI env vars
                   (defaults to False = dev / watch-it-click). Pass
                   True/False explicitly to override.

    Returns:
        dict with keys: tool, vlan_id, vlan_name, snapshot_pre,
        snapshot_post, screenshots, verified.

    Raises:
        NotApproved:            action not in APPROVED state
        SubprocessFlowError:    Playwright child process failed
        WebUIVerificationError: WebUI clicked Save but CLI doesn't see the VLAN
    """
    _guard(action_id)

    log.info(
        "add_access_vlan_via_webui_start",
        vlan_id=vlan_id,
        vlan_name=vlan_name,
        action_id=action_id,
    )

    # Pre-snapshot via SSH (independent of the WebUI session)
    pre_dir = take_snapshot(action_id, "pre")

    try:
        child_result = run_flow_in_subprocess(
            "add_access_vlan",
            {
                "vlan_id": vlan_id,
                "vlan_name": vlan_name,
                "action_id": action_id,
                "headless": headless,
            },
        )

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
            screenshots=child_result.get("screenshots"),
        )

        return {
            "tool": "webui_add_access_vlan",
            "vlan_id": vlan_id,
            "vlan_name": vlan_name,
            "snapshot_pre": str(pre_dir),
            "snapshot_post": str(post_dir),
            "screenshots": child_result.get("screenshots"),
            "verified": True,
        }

    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "add_access_vlan_via_webui_failed",
            action_id=action_id,
            error=str(exc),
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        raise
