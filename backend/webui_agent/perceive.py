"""Perceive — hot-path page observation via accessibility tree + atlas.

``perceive_page`` does exactly:
  1. One ``page.accessibility.snapshot()`` call.
  2. One ``flatten_interactive`` + ``reconcile`` pass against the stored atlas.
  3. If drift is detected (and self_verify is True, and we didn't just capture),
     one fresh ``capture_route`` + one more snapshot/flatten/reconcile.

No screenshots, no networkidle, no per-element round-trips.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.logging import get_logger
from backend.webui_agent.atlas.capture import capture_route
from backend.webui_agent.atlas.fingerprint import route_slug
from backend.webui_agent.atlas.reconcile import flatten_interactive, reconcile
from backend.webui_agent.atlas.schema import RouteAtlas
from backend.webui_agent.atlas.store import AtlasStore

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def get_live_url(page: Page) -> str:
    """Return the live URL via JS — handles Angular hash routes.

    ``page.url`` does NOT update on Angular hash-router navigation;
    ``window.location.href`` always reflects the current fragment.
    Falls back to ``""`` on any error.
    """
    try:
        result = page.evaluate("() => window.location.href")
        return str(result) if result is not None else ""
    except Exception:
        return ""


def route_from_url(url: str) -> str:
    """Extract the route key from a URL for atlas store lookup.

    Returns the ``#/...`` fragment if present (e.g. ``"#/ospf"``),
    otherwise falls back to the full url (stripped).

    The returned value is what gets passed to ``store.load_route`` /
    ``store.save_route`` — it can be any non-empty string; the store
    normalises it to a slug internally.
    """
    if not url:
        return ""
    # Extract fragment.
    if "#" in url:
        fragment = url.split("#", 1)[1]
        if fragment:
            return "#" + fragment
    return url


# ---------------------------------------------------------------------------
# PerceiveResult
# ---------------------------------------------------------------------------


@dataclass
class PerceiveResult:
    """Output of :func:`perceive_page`."""

    view: dict
    atlas: RouteAtlas
    drift: bool
    captured: bool
    missing_required: list[str] = field(default_factory=list)
    unmapped_fields: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# perceive_page
# ---------------------------------------------------------------------------


def perceive_page(
    page: Page,
    store: AtlasStore,
    *,
    device_fingerprint: str,
    route: str | None = None,
    self_verify: bool = True,
) -> PerceiveResult:
    """Observe the current page via accessibility tree + atlas reconciliation.

    Steps
    -----
    1. Derive the route key from the live URL (or use the supplied ``route``).
    2. Load the atlas from the store; if missing, capture it from the DOM.
    3. Take one ``page.accessibility.snapshot()`` and flatten + reconcile.
    4. If drift is detected and ``self_verify`` is True (and we didn't just
       capture): re-capture once, increment ``drift_count``, save, then
       re-snapshot + re-reconcile ONCE (no infinite loop).
    5. On agreement after verify, increment ``verify_count`` and save.

    Exactly ONE ``page.accessibility.snapshot()`` per pass (two passes only
    when drift triggers self-verify).
    """
    # --- Step 1: resolve route ---
    if route is None:
        route = route_from_url(get_live_url(page))
    if not route:
        route = "unknown"

    # Derive a clean slug for logging.
    slug = route_slug(route)

    # --- Step 2: load or capture atlas ---
    captured = False
    atlas = store.load_route(route)
    if atlas is None:
        atlas = capture_route(
            page,
            route=route,
            device_fingerprint=device_fingerprint,
        )
        store.save_route(atlas)
        captured = True

    # --- Step 3: first accessibility snapshot + reconcile ---
    snap = page.accessibility.snapshot(interesting_only=True)
    live = flatten_interactive(snap)
    rec = reconcile(atlas, live)

    # --- Step 4: self-verify on drift (guard: only once, only if not just captured) ---
    if rec.drift and self_verify and not captured:
        atlas = capture_route(
            page,
            route=route,
            device_fingerprint=device_fingerprint,
        )
        atlas.drift_count += 1
        store.save_route(atlas)

        # One more snapshot pass.
        snap2 = page.accessibility.snapshot(interesting_only=True)
        live2 = flatten_interactive(snap2)
        rec = reconcile(atlas, live2)

        # --- Step 5: on agreement after re-capture, bump verify_count ---
        if not rec.drift:
            atlas.verify_count += 1
            with contextlib.suppress(Exception):
                store.save_route(atlas)

    logger.info(
        "perceive_complete",
        route=slug,
        field_count=len(rec.view.get("fields", [])),
        drift=rec.drift,
        captured=captured,
    )

    return PerceiveResult(
        view=rec.view,
        atlas=atlas,
        drift=rec.drift,
        captured=captured,
        missing_required=rec.missing_required,
        unmapped_fields=rec.unmapped_fields,
    )
