"""Semantic DOM walker for the Cisco IOS XE WebUI.

Phase 3 of the AI-first WebUI v0.4.0 plan (see
[docs/plan-ai-first-webui.md](../../docs/plan-ai-first-webui.md)).
Produces a token-bounded snapshot of the current Playwright Page so the
planner (Claude) can decide which element to click without us hand-coding
a Page Object Model per feature.

Output shape (JSON-serialisable). Typical button-heavy page ~700 tokens;
worst case 30 textboxes with max-length labels + value/required ≈ 1400
tokens — still under 1% of Haiku 4.5's input window and ~$0.001 per call.

    {
      "view_id": "a3f9b2e1",   # 8-hex per-call id; see "Eid staleness" below
      "url": str,
      "title": str,
      "elements": [
          {"eid": "e_001", "role": "button", "name": "Add",
           "enabled": True, "bbox": [x, y, w, h]},
          {"eid": "e_002", "role": "textbox", "name": "Host Name*",
           "enabled": True, "bbox": [...], "value": "LAB-R4", "required": True},
          {"eid": "e_003", "role": "combobox", "name": "VLAN List",
           "enabled": True, "bbox": [...], "value": "VLAN46"},
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

**Eid staleness**: `eid` strings (`e_001`, `e_002`, ...) are positional in
the score-sorted candidate list — they are NOT stable across describe
calls. A re-describe (e.g. after a navigation or self-heal) renumbers
every element. To detect a stale planner reference, every view carries
a random `view_id` (8 hex chars). Phase 4's `webui_act(view_id, eid, …)`
must reject when the supplied view_id is not the most recent — forcing
the planner to re-describe before acting.

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

import uuid
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

# Truncate accessible names to keep token budget bounded (~18 tokens / element).
# 50 covers "Maximum Number of Equal Cost Multipath Routes" (47 chars) — the
# longest real Cisco label observed so far. 80 was over budget at worst case.
_MAX_NAME_LEN = 50

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

    # view_id is a per-call cookie so Phase 4's webui_act can reject stale eid
    # references (eids renumber on every describe). 8 hex chars from uuid4
    # give ~4 billion permutations — plenty for the lifetime of one session.
    view_id = uuid.uuid4().hex[:8]

    view: dict[str, Any] = {
        "view_id": view_id,
        "url": url,
        "title": title,
        "elements": elements_out,
        "modals": modals_out,
        "errors": errors_out,
    }

    log.debug(
        "describe_page_complete",
        view_id=view_id,
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

    # value and required let Phase 4's propose_webui_configure reason about
    # pre-filled form state on a re-describe (don't overwrite already-correct
    # fields). Only emitted for the roles that actually carry these concepts.
    role = cand["role"]
    if role in ("textbox", "combobox"):
        loc = cand["loc"]
        value = _safe_call(lambda: loc.input_value(timeout=_PROBE_TIMEOUT_MS), default="")
        if isinstance(value, str):
            out["value"] = value
    if role == "textbox":
        # HTML5 boolean attr: present (even with empty string) means required.
        loc = cand["loc"]
        out["required"] = _safe_attr(loc, "required") is not None
    return out


def _spatial_label(loc: Locator) -> str:
    """Find a nearby visible text element acting as this input's label.

    Searches two layouts Cisco's WebUI Angular forms actually use:

      (A) Label ABOVE input — typical floating-label pattern:
              <span>Prefix Mask</span>
              <input placeholder="xxx.xxx.xxx.xxx">

      (B) Label LEFT of input — table-row / inline-form pattern:
              <span>Prefix Mask</span><input ...>

    Earlier versions only searched (A) with a 300px dy window, which on
    the Static Routing form was loose enough that the algorithm walked
    past the actual row-label and grabbed a column-header text from the
    table ABOVE the form — producing the "shifted-by-one-row" labeling
    bug (e.g. labeling the Prefix input "IP Type" because IP Type's
    column header was the closest aligned text above). Tighter dy + a
    left-of-input pass fix that.

    Tight thresholds (~80px above, ~200px left) match the typical
    label-gap in dense forms; column headers from a table further up
    the page no longer qualify.

    Returns "" if no candidate found or on any error. Single
    ``page.evaluate`` call to keep round-trip cost flat.
    """
    try:
        bbox = loc.bounding_box(timeout=_PROBE_TIMEOUT_MS)
    except Exception as exc:
        log.debug("spatial_label_bbox_failed", error=str(exc))
        return ""
    if not bbox:
        return ""
    target = {
        "x": bbox.get("x", 0),
        "y": bbox.get("y", 0),
        "width": bbox.get("width", 0),
        "height": bbox.get("height", 0),
    }
    if target["width"] == 0 or target["height"] == 0:
        return ""

    # JS-side search. Two layouts considered; cheaper score wins.
    js = """
    (t) => {
      const sel = 'label,span,div,a,p,td,th,h1,h2,h3,h4,h5,h6,strong,em';
      const cs = document.querySelectorAll(sel);
      let bestText = null;
      let bestScore = Infinity;
      const inputEnd = t.x + t.width;
      const inputCenterY = t.y + t.height / 2;
      for (const el of cs) {
        // Skip containers that wrap form elements (they own layout, not labels).
        if (el.querySelector('input, textarea, select, button')) continue;
        // Skip table header elements and anything whose ancestor chain contains
        // <th> or <thead>. This catches both:
        //   (a) <th> elements themselves (el.tagName check), and
        //   (b) inline elements such as <a> or <span> nested inside a <th>
        //       (el.closest check).
        // The Cisco DHCP list view renders "Network/Subnet Mask" as a <th>
        // column header spatially above the Create DHCP Pool modal. Without
        // this guard the spatial-label search picks it up instead of the
        // <span class="label">Network</span> in the modal.
        if (el.tagName === 'TH' || el.tagName === 'THEAD') continue;
        if (el.closest('th, thead')) continue;
        const text = (el.innerText || el.textContent || '').trim();
        if (!text || text.length > 60) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;

        // Layout A: label ABOVE the input.
        // dy = gap between label bottom and input top.
        const dy = t.y - (r.y + r.height);
        if (dy >= 0 && dy <= 80) {
          const labelEnd = r.x + r.width;
          const overlap = Math.max(0, Math.min(labelEnd, inputEnd) - Math.max(r.x, t.x));
          let alignCost;
          if (overlap > 0) {
            alignCost = 0;
          } else {
            alignCost = Math.min(Math.abs(r.x - t.x), Math.abs(labelEnd - t.x));
            if (alignCost > 60) continue;
          }
          const score = dy + alignCost / 3;
          if (score < bestScore) {
            bestScore = score;
            bestText = text;
          }
          continue;
        }

        // Layout B: label LEFT of the input on the SAME row.
        // Same row = label's vertical center within ±20px of input center.
        const labelCenterY = r.y + r.height / 2;
        const dyCenter = Math.abs(inputCenterY - labelCenterY);
        const labelEnd2 = r.x + r.width;
        const dxGap = t.x - labelEnd2;
        if (dyCenter <= 20 && dxGap >= 0 && dxGap <= 200) {
          // Score: horizontal gap. Add small same-row penalty vs ABOVE so
          // a tight ABOVE label still wins ties.
          const score = dxGap + 5;
          if (score < bestScore) {
            bestScore = score;
            bestText = text;
          }
        }
      }
      return bestText;
    }
    """
    try:
        result = loc.page.evaluate(js, target)
    except Exception as exc:
        log.debug("spatial_label_evaluate_failed", error=str(exc))
        return ""
    if not isinstance(result, str) or not result.strip():
        return ""
    return result.strip()[:_MAX_NAME_LEN]


def _resolve_name(loc: Locator) -> str:
    """Walk the name-resolution chain for a Playwright Locator.

    Steps (Phase 3.4):
      1. aria-label
      2. aria-labelledby
      3. inner_text
      4. <label for="id"> association
      5. spatial label (Phase 3.4) — nearest text element above the input
      6. placeholder
      7. title
      8. name attribute
      9. id (skip ng-* auto-generated Angular ids)
    """
    # 1. aria-label
    aria_label = _safe_attr(loc, "aria-label")
    if aria_label and aria_label.strip():
        return aria_label.strip()[:_MAX_NAME_LEN]

    # 2. aria-labelledby
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

    # 3. inner_text
    inner = _safe_call(lambda: loc.inner_text(timeout=_PROBE_TIMEOUT_MS), default="")
    if inner and isinstance(inner, str) and inner.strip():
        return inner.strip()[:_MAX_NAME_LEN]

    # 4. <label for="id"> association: standard HTML form labeling pattern.
    #    Cisco's Angular forms use this when aria-labelledby isn't wired.
    input_id = _safe_attr(loc, "id")
    if input_id:
        try:
            label_text = loc.page.locator(f'label[for="{input_id}"]').inner_text(
                timeout=_PROBE_TIMEOUT_MS
            )
            if label_text and isinstance(label_text, str) and label_text.strip():
                return label_text.strip()[:_MAX_NAME_LEN]
        except Exception as exc:
            log.debug("label_for_resolve_failed", input_id=input_id, error=str(exc))

    # 5. Spatial label discovery (Phase 3.4): visually nearby text element.
    #    Catches Cisco's table-form pattern where labels are siblings of inputs
    #    without <label for=> association. Returns early on hit so we override
    #    the (often uninformative) placeholder/name/id fallbacks below.
    spatial = _spatial_label(loc)
    if spatial:
        return spatial

    # 6. placeholder (used only when spatial returned empty).
    placeholder = _safe_attr(loc, "placeholder")
    if placeholder and placeholder.strip():
        return placeholder.strip()[:_MAX_NAME_LEN]

    # 7. title attribute (tooltip on hover) — semantic but only visible on hover.
    title = _safe_attr(loc, "title")
    if title and title.strip():
        return title.strip()[:_MAX_NAME_LEN]

    # 8. name attribute (form-field identifier) — usually camelCase/snake_case
    #    but readable enough as a last semantic resort.
    name_attr = _safe_attr(loc, "name")
    if name_attr and name_attr.strip():
        return name_attr.strip()[:_MAX_NAME_LEN]

    # 9. id attribute, BUT skip Angular-autogenerated `ng-*` IDs (e.g.
    #    "ng-1234abc") — those carry no semantic meaning.
    if input_id and not input_id.startswith("ng-"):
        return input_id.strip()[:_MAX_NAME_LEN]

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
