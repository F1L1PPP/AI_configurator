"""Semantic DOM walker for the Cisco IOS XE WebUI.

Phase 3 of the AI-first WebUI v0.4.0 plan (see
[docs/plan-ai-first-webui.md](../../docs/plan-ai-first-webui.md)).
Produces a token-bounded snapshot of the current Playwright Page so the
planner (Claude) can decide which element to click without us hand-coding
a Page Object Model per feature.

Output shape (JSON-serialisable, designed to fit in ~800 tokens):

    {
      "url": str,
      "title": str,
      "elements": [
          {"eid": "e_001", "role": "button", "name": "Add",
           "enabled": True, "bbox": [x, y, w, h]},
          ...
      ],
      "modals": [...same shape, role in {dialog, alertdialog}, eid "m_NNN"...],
      "errors": [{"name": "VLAN already exists"}],
    }

`elements` is capped at `max_elements` (default 30), sorted by
`visibility * enabled_bonus * centrality * size_penalty`. Visibility is
implicit — only visible elements are emitted, so no `visible` field is
serialised. `bbox` values are rounded to int (sub-pixel precision is
meaningless for click targets and bloats the token budget).

The companion `locator_map` returned alongside maps each `eid` back to
its Playwright Locator for follow-up `webui_act(eid, ...)` calls in
Phase 4. The map is NOT cached on the Page or globally — it is scoped
to the calling subprocess invocation, since Playwright Locators don't
cross the subprocess boundary.

Known limitation: iframes are not walked. The Cisco IOS XE 17.x WebUI is
single-frame Angular (verified by grep at v0.4.0 phase 3 lands). If a
future device introduces iframes, describe_page silently omits elements
inside them.

This module runs INSIDE the Playwright child process spawned by
[_subprocess.py](_subprocess.py). Phase 4's action tools will invoke
it from inside the same dispatch handler that runs the action — there is
no point describing the page in the parent and acting in the child.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.logging import get_logger

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# CSS union selector — every native tag + ARIA role we treat as interactive.
# One locator round-trip rather than 12 separate get_by_role calls; the Cisco
# WebUI is Angular with mixed-quality ARIA, so we want both native tags AND
# role attributes to catch the same element via either path.
_UNION_SELECTOR = ",".join(
    [
        "button",
        '[role="button"]',
        'input:not([type="hidden"])',
        "select",
        "textarea",
        "a[href]",
        '[role="textbox"]',
        '[role="combobox"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="tab"]',
        '[role="link"]',
        '[role="menuitem"]',
        '[role="dialog"]',
        '[role="alertdialog"]',
        '[role="alert"]',
    ]
)

# Truncate accessible names to keep token budget bounded (~28 tokens / element).
_MAX_NAME_LEN = 80

# Fallback when page.viewport_size returns None. Matches browser.py default.
# Typed as dict[str, Any] so Playwright's ViewportSize TypedDict slots in.
_DEFAULT_VIEWPORT: dict[str, Any] = {"width": 1400, "height": 900}

# Short per-element probe timeout. We accept missing data over a hang — the
# planner can always re-call describe_page if a flaky element is needed.
_PROBE_TIMEOUT_MS = 200

# Role classification — native tag → ARIA role for tags we can map directly.
_TAG_ROLE_MAP: dict[str, str] = {
    "button": "button",
    "select": "combobox",
    "textarea": "textbox",
    "a": "link",
}

# Role classification — <input type="..."> → ARIA role.
_INPUT_TYPE_ROLE_MAP: dict[str, str] = {
    "text": "textbox",
    "email": "textbox",
    "tel": "textbox",
    "url": "textbox",
    "search": "textbox",
    "number": "textbox",
    "password": "textbox",
    "checkbox": "checkbox",
    "radio": "radio",
    "submit": "button",
    "reset": "button",
    "button": "button",
}

_MODAL_ROLES = frozenset({"dialog", "alertdialog"})
_ALERT_ROLES = frozenset({"alert"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def describe_page(
    page: Page, *, max_elements: int = 30
) -> tuple[dict[str, Any], dict[str, Locator]]:
    """Walk the current page and return a token-bounded semantic view + locator map.

    Returns:
        (view, locator_map) where ``view`` is the JSON-serialisable dict above
        and ``locator_map`` maps each emitted ``eid`` back to its Playwright
        ``Locator``. Caller owns the map; it must not outlive the page.

    The view's ``elements`` array is sorted by score descending and capped at
    ``max_elements`` (default 30). ``modals`` and ``errors`` are emitted
    separately and not subject to the cap — there are rarely more than one
    or two of either on a real page.
    """
    # Coerce Playwright's ViewportSize TypedDict to a plain dict so the
    # helper signatures stay simple.
    viewport: dict[str, Any] = dict(page.viewport_size or _DEFAULT_VIEWPORT)

    url = _safe_call(lambda: page.url, default="")
    title = _safe_call(page.title, default="")

    all_locators: list[Locator] = page.locator(_UNION_SELECTOR).all()

    candidates: list[dict[str, Any]] = []
    for loc in all_locators:
        visible = _safe_bool(loc.is_visible, default=False)
        if not visible:
            continue

        bbox = _safe_bbox(loc)
        enabled = _safe_bool(loc.is_enabled, default=False)
        role = _classify_role(loc)
        name = _resolve_name(loc)
        score = _score(bbox, visible, enabled, viewport)

        candidates.append(
            {
                "loc": loc,
                "role": role,
                "name": name,
                "visible": visible,
                "enabled": enabled,
                "bbox": bbox,
                "score": score,
            }
        )

    modal_candidates = [c for c in candidates if c["role"] in _MODAL_ROLES]
    error_candidates = [c for c in candidates if c["role"] in _ALERT_ROLES]
    element_candidates = [
        c
        for c in candidates
        if c["role"] not in _MODAL_ROLES
        and c["role"] not in _ALERT_ROLES
        and c["role"] != "unknown"
        and c["score"] > 0
    ]

    element_candidates.sort(key=lambda c: c["score"], reverse=True)
    element_candidates = element_candidates[:max_elements]

    locator_map: dict[str, Locator] = {}
    elements_out: list[dict[str, Any]] = []
    for i, cand in enumerate(element_candidates, start=1):
        eid = f"e_{i:03d}"
        locator_map[eid] = cand["loc"]
        elements_out.append(_serialise(eid, cand))

    modals_out: list[dict[str, Any]] = []
    for i, cand in enumerate(modal_candidates, start=1):
        eid = f"m_{i:03d}"
        locator_map[eid] = cand["loc"]
        modals_out.append(_serialise(eid, cand))

    # Errors are name-only — they're informational and not addressable. No eid.
    errors_out = [{"name": c["name"]} for c in error_candidates if c["name"]]

    view: dict[str, Any] = {
        "url": url,
        "title": title,
        "elements": elements_out,
        "modals": modals_out,
        "errors": errors_out,
    }

    log.debug(
        "describe_page_complete",
        url=url,
        elements=len(elements_out),
        modals=len(modals_out),
        errors=len(errors_out),
        total_candidates=len(candidates),
    )
    return view, locator_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialise(eid: str, cand: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "eid": eid,
        "role": cand["role"],
        "name": cand["name"],
        "enabled": cand["enabled"],
    }
    bbox = cand["bbox"]
    if bbox is not None:
        # Int rounding: sub-pixel precision is meaningless for click targets
        # and would bloat the token budget by ~12 chars per element.
        out["bbox"] = [
            int(round(bbox["x"])),
            int(round(bbox["y"])),
            int(round(bbox["width"])),
            int(round(bbox["height"])),
        ]
    return out


def _resolve_name(loc: Locator) -> str:
    """Walk aria-label -> aria-labelledby -> inner_text -> placeholder."""
    aria_label = _safe_attr(loc, "aria-label")
    if aria_label and aria_label.strip():
        return aria_label.strip()[:_MAX_NAME_LEN]

    labelledby = _safe_attr(loc, "aria-labelledby")
    if labelledby:
        ref_id = labelledby.split()[0]
        try:
            page = loc.page
            txt = page.locator(f"#{ref_id}").inner_text(timeout=_PROBE_TIMEOUT_MS)
            if txt and isinstance(txt, str) and txt.strip():
                return txt.strip()[:_MAX_NAME_LEN]
        except Exception as exc:
            log.debug("labelledby_resolve_failed", ref=ref_id, error=str(exc))

    inner = _safe_call(lambda: loc.inner_text(timeout=_PROBE_TIMEOUT_MS), default="")
    if inner and isinstance(inner, str) and inner.strip():
        return inner.strip()[:_MAX_NAME_LEN]

    placeholder = _safe_attr(loc, "placeholder")
    if placeholder and placeholder.strip():
        return placeholder.strip()[:_MAX_NAME_LEN]

    return ""


def _classify_role(loc: Locator) -> str:
    """Pick the element's effective ARIA role.

    Explicit ``role`` attribute wins; otherwise infer from tagName +
    (for ``<input>``) type.
    """
    explicit = _safe_attr(loc, "role")
    if explicit and explicit.strip():
        return explicit.strip().lower()

    tag = _safe_call(
        lambda: loc.evaluate("el => el.tagName", timeout=_PROBE_TIMEOUT_MS),
        default="",
    )
    if not isinstance(tag, str):
        return "unknown"
    tag = tag.lower()

    if tag == "input":
        itype = (_safe_attr(loc, "type") or "text").lower()
        return _INPUT_TYPE_ROLE_MAP.get(itype, "textbox")

    return _TAG_ROLE_MAP.get(tag, "unknown")


def _score(
    bbox: dict[str, Any] | None,
    visible: bool,
    enabled: bool,
    viewport: dict[str, Any],
) -> float:
    """Composite score; higher = more likely to be the user's target.

    Hidden elements (visible=False) or unmeasurable bbox -> 0 (dropped).
    """
    if not visible or bbox is None:
        return 0.0

    cy = bbox["y"] + bbox["height"] / 2.0
    vh = float(viewport.get("height", _DEFAULT_VIEWPORT["height"]))
    vcy = vh / 2.0
    centrality = max(0.0, 1.0 - abs(cy - vcy) / vh)

    area = bbox["width"] * bbox["height"]
    size_penalty = 1.0 if 200 < area < 50_000 else 0.5

    enabled_bonus = 1.0 if enabled else 0.3

    return centrality * size_penalty * enabled_bonus


def _safe_attr(loc: Locator, name: str) -> str | None:
    try:
        return loc.get_attribute(name, timeout=_PROBE_TIMEOUT_MS)
    except Exception as exc:
        log.debug("get_attribute_failed", attr=name, error=str(exc))
        return None


def _safe_bbox(loc: Locator) -> dict[str, Any] | None:
    try:
        # Playwright returns a FloatRect TypedDict (or None); coerce to dict[str, Any]
        # so the rest of the module can treat it uniformly with the fallback.
        rect = loc.bounding_box(timeout=_PROBE_TIMEOUT_MS)
        return dict(rect) if rect is not None else None
    except Exception as exc:
        log.debug("bounding_box_failed", error=str(exc))
        return None


def _safe_bool(fn: Any, *, default: bool) -> bool:
    try:
        return bool(fn())
    except Exception:
        return default


def _safe_call(fn: Any, *, default: Any) -> Any:
    try:
        return fn()
    except Exception:
        return default
