"""Perceive — hot-path page observation via direct DOM extraction.

``perceive_page`` does exactly:
  1. Derive route from the live URL (JS eval).
  2. Extract descriptors via one ``page.evaluate(_CAPTURE_JS)`` call.
     If zero real form-field descriptors are returned, wait 600 ms and
     re-extract once (Angular may not have finished rendering).
  3. Build atlas + save to store (best-effort cache).
  4. Build view directly from those descriptors (DOM-keyed, no a11y reconcile).
  5. Return PerceiveResult.

No accessibility.snapshot calls, no flatten_interactive, no reconcile in the
hot path.  The a11y helpers still exist and are used by verify_a11y in the
subprocess; they are just NOT called here.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.logging import get_logger
from backend.webui_agent.atlas.capture import (
    build_atlas,
    extract_descriptors,
    view_from_descriptors,
)
from backend.webui_agent.atlas.fingerprint import route_slug
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
# Internal helpers
# ---------------------------------------------------------------------------


def _has_real_controls(descriptors: list[dict]) -> bool:
    """Return True if the descriptor list contains at least one real form field
    or apply/open-form control (i.e. Angular has rendered the form content).
    """
    from backend.webui_agent.atlas.capture import (  # noqa: PLC0415
        _FORM_FIELD_WIDGETS,
        _classify_role,
        _has_stable_identity,
        _is_form_control,
        classify_widget,
        is_apply_control,
        is_open_form_control,
    )

    for desc in descriptors:
        widget = classify_widget(desc)
        role = _classify_role(desc)
        if widget == "button" and (is_apply_control(desc) or is_open_form_control(desc)):
            return True
        if widget in _FORM_FIELD_WIDGETS and _is_form_control(desc, role) and _has_stable_identity(desc):
            return True
    return False


def _extract_with_retry(page: Page) -> list[dict]:
    """Extract descriptors; if none are real controls, wait 600 ms and retry once."""
    descriptors = extract_descriptors(page)
    if not _has_real_controls(descriptors):
        with contextlib.suppress(Exception):
            page.wait_for_timeout(600)
        descriptors = extract_descriptors(page)
    return descriptors


# ---------------------------------------------------------------------------
# perceive_page
# ---------------------------------------------------------------------------


def perceive_page(
    page: Page,
    store: AtlasStore,
    *,
    device_fingerprint: str,
    route: str | None = None,
    self_verify: bool = True,  # kept for API compat; no longer triggers a11y reconcile
) -> PerceiveResult:
    """Observe the current page via DOM extraction (no accessibility.snapshot).

    Steps
    -----
    1. Derive the route key from the live URL (or use the supplied ``route``).
    2. Extract descriptors with ``_extract_with_retry`` (one evaluate + optional
       one retry after 600 ms if Angular hasn't rendered yet).
    3. Fetch page title (best-effort).
    4. Build ``RouteAtlas`` and save to store (best-effort cache; never raises).
    5. Build perceive VIEW directly from descriptors (DOM-keyed).
    6. Compute ``missing_required`` — required fields whose current value is empty.
    7. Return ``PerceiveResult``.

    No ``page.accessibility.snapshot()`` is called.  The ``self_verify`` parameter
    is accepted for API compatibility but does not trigger an a11y reconcile pass.
    """
    # --- Step 1: resolve route ---
    if route is None:
        route = route_from_url(get_live_url(page))
    if not route:
        route = "unknown"

    slug = route_slug(route)

    # --- Step 2: extract descriptors (with Angular-render retry) ---
    descriptors = _extract_with_retry(page)

    # --- Step 3: page title (best-effort) ---
    page_title = ""
    with contextlib.suppress(Exception):
        page_title = page.title()

    fp = device_fingerprint

    # --- Step 4: build atlas + save to store (best-effort cache) ---
    atlas = build_atlas(descriptors, route=route, device_fingerprint=fp, page_title=page_title)
    with contextlib.suppress(Exception):
        store.save_route(atlas)

    # --- Step 5: build view from descriptors (DOM-keyed, no a11y reconcile) ---
    view = view_from_descriptors(
        descriptors, route=route, device_fingerprint=fp, page_title=page_title
    )

    # --- Step 6: missing_required ---
    view_field_values: dict[str, str] = {
        f["key"]: (f.get("value") or "") for f in view.get("fields", [])
    }
    missing_required = [
        fs.key
        for fs in atlas.fields
        if fs.required and not view_field_values.get(fs.key, "")
    ]

    field_keys = [f["key"] for f in view.get("fields", [])]
    logger.info(
        "perceive_complete",
        route=slug,
        field_count=len(view.get("fields", [])),
        field_keys=field_keys,
        drift=False,
        captured=True,
    )

    return PerceiveResult(
        view=view,
        atlas=atlas,
        drift=False,
        captured=True,
        missing_required=missing_required,
        unmapped_fields=[],
    )
