"""Screenshot + DOM evidence collector for WebUI flow runs.

Implements PROJECT_PLAN.md §6.4: every WebUI step takes a screenshot under
`artifacts/screenshots/<session>/`, and on any error we also dump the page
DOM so the failed selector can be analyzed offline.

One `EvidenceCollector` per flow run. Numbering is automatic; just pass a
human-readable label per step.

Usage:
    ev = EvidenceCollector("change_hostname", action_id=approved_action_id)
    ev.step("01-login-page", page)
    ev.step("02-credentials-filled", page)
    try:
        ...
    except Exception:
        ev.dump_dom(page, label="99-exception")
        raise
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.core.logging import get_logger
from backend.core.settings import get_settings

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = get_logger(__name__)


class EvidenceCollector:
    """Numbered-screenshot + DOM-dump helper for one WebUI flow run."""

    def __init__(self, flow_name: str, action_id: str | None = None) -> None:
        # Prefer the action_id as the session id so all evidence for one
        # approved action lives in one folder. Fall back to a timestamp.
        suffix = action_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.session_dir = get_settings().artifacts_dir / "screenshots" / f"{flow_name}_{suffix}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._n = 0
        self.vision_call_count: int = 0
        self.plan_vision_count: int = 0  # separate from vision_call_count (14b)
        log.info(
            "evidence_session_started",
            flow=flow_name,
            action_id=action_id,
            path=str(self.session_dir),
        )

    def step(self, label: str, page: Page) -> Path:
        """Full-page screenshot. Auto-numbers the filename so the order on
        disk matches the call order. Returns the saved path."""
        self._n += 1
        path = self.session_dir / f"{self._n:02d}-{label}.png"
        page.screenshot(path=str(path), full_page=True)
        log.info("evidence_step", n=self._n, label=label, path=str(path))
        return path

    def vision_screenshot(self, page: Any, intent_id: str) -> Path:
        """Save ad-hoc PNG without advancing the step counter.

        Used by vision_fallback for the API call screenshot. Filename:
        vision-{intent_id}.png — keyed by intent so multiple fallbacks on
        the same flow don't collide.
        """
        path = self.session_dir / f"vision-{intent_id}.png"
        page.screenshot(path=str(path), full_page=True)
        return path

    def dump_dom(self, page: Page, label: str = "dom") -> Path:
        """Save the current page HTML. Use this on failure so the
        live DOM can be inspected against the selectors yaml."""
        path = self.session_dir / f"{label}.html"
        path.write_text(page.content(), encoding="utf-8")
        log.warning("evidence_dom_dump", label=label, path=str(path))
        return path

    @property
    def step_count(self) -> int:
        return self._n
