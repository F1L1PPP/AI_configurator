"""Atlas capture — build a RouteAtlas from one batched DOM extraction.

``capture_route(page, ...)`` does exactly ONE ``page.evaluate(_CAPTURE_JS)``
call (plus ``page.title()`` if no page_title is supplied) and derives the
full :class:`~backend.webui_agent.atlas.schema.RouteAtlas` from the returned
descriptor list.  No per-element round-trips, no screenshots, no networkidle.

Design goals
------------
- ``extract_descriptors`` owns all page I/O.  Everything below it is pure
  Python that works on plain dicts — unit-testable without a browser.
- ``classify_widget``, ``resolve_label``, ``resolve_key``, ``build_locator``,
  ``is_apply_control``, ``is_open_form_control`` are all pure functions so
  they can be imported and tested independently.
- ``build_atlas`` assembles a :class:`RouteAtlas` from a list of descriptors;
  ``capture_route`` glues it to the page.
- ``view_from_descriptors`` builds the perceive VIEW directly from descriptors,
  keyed by field_key (DOM-keyed), without any accessibility-tree reconcile.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.core.logging import get_logger
from backend.webui_agent.atlas.fingerprint import slugify
from backend.webui_agent.atlas.schema import (
    ControlSpec,
    FieldSpec,
    LocatorSpec,
    RouteAtlas,
    SuccessSignal,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Apply-glyph constants (ported from semantic_dom.py)
# ---------------------------------------------------------------------------

_APPLY_ICON_CLASSES: tuple[str, ...] = ("pl-save", "icon-save-device")
_APPLY_PRIMARY_CLASS = "primaryActionButton"

# Labels that indicate an "open form" / "add new row" control.
_OPEN_FORM_LABELS: frozenset[str] = frozenset({"add", "add new", "create", "new", "+"})

# Tag → ARIA role mapping (mirrors semantic_dom._TAG_ROLE_MAP).
_TAG_ROLE_MAP: dict[str, str] = {
    "button": "button",
    "select": "combobox",
    "textarea": "textbox",
    "a": "link",
}

# input[type] → ARIA role mapping (mirrors semantic_dom._INPUT_TYPE_ROLE_MAP).
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

# Form-field widget types (not buttons/links).
_FORM_FIELD_WIDGETS: frozenset[str] = frozenset(
    {"input", "kendo_combobox", "kendo_numeric", "checkbox", "radio", "kendo_grid"}
)

# Genuine form-control gate. classify_widget's catch-all maps ANY unmatched
# interactive element to "input", but the union selector also pulls navigation
# <a href> links, role="tab", role="menuitem", and dialog/alert containers.
# Without this gate every visible nav link/tab on a real page would be emitted
# as a FieldSpec, flooding the atlas. A field is a native form tag, OR carries a
# form-field ARIA role, OR is a Kendo widget backed by a hidden <select>.
_FORM_CONTROL_TAGS: frozenset[str] = frozenset({"input", "select", "textarea"})
_FORM_CONTROL_ROLES: frozenset[str] = frozenset(
    {"textbox", "combobox", "listbox", "checkbox", "radio", "spinbutton"}
)


def _is_form_control(desc: dict, role: str) -> bool:
    """True only for genuine form controls (not links/tabs/menuitems/dialogs)."""
    tag = (desc.get("tag") or "").lower()
    if tag in _FORM_CONTROL_TAGS:
        return True
    if role in _FORM_CONTROL_ROLES:
        return True
    # A Kendo dropdown backed by a hidden <select> is a form control even if its
    # visible wrapper's role is unusual.
    return bool(desc.get("kendo_select_name"))

# ---------------------------------------------------------------------------
# _CAPTURE_JS — single evaluate call that returns all element descriptors.
# ---------------------------------------------------------------------------
#
# Ports from semantic_dom.py:
#   * _UNION_SELECTOR  — the CSS query that selects all interactive elements
#   * _spatial_label   — A/B layout algorithm for finding a nearby label
#   * _resolve_kendo_select_name — finds backing hidden <select> for Kendo widgets
#
# Each element yields a descriptor dict with all fields needed to classify,
# label, key, and locate the element without additional page round-trips.

_CAPTURE_JS = """
() => {
  // -----------------------------------------------------------------------
  // Union selector — ported from semantic_dom._UNION_SELECTOR
  // -----------------------------------------------------------------------
  const UNION_SEL = [
    'button',
    '[role="button"]',
    'input:not([type="hidden"])',
    'select',
    'textarea',
    'a[href]',
    '[role="textbox"]',
    '[role="combobox"]',
    '[role="listbox"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="tab"]',
    '[role="link"]',
    '[role="menuitem"]',
    '[role="dialog"]',
    '[role="alertdialog"]',
    '[role="alert"]',
    '[ng-click]:has(> .pl-save)',
    '[ng-click]:has(> .icon-save-device)'
  ].join(',');

  // -----------------------------------------------------------------------
  // Visibility check
  // -----------------------------------------------------------------------
  function isVisible(el) {
    const rects = el.getClientRects();
    if (!rects || rects.length === 0) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    return true;
  }

  // -----------------------------------------------------------------------
  // Spatial label — ported from semantic_dom._spatial_label JS body
  // Searches for a nearby text element above (Layout A) or to the left
  // (Layout B) of the given bounding rect t.
  // -----------------------------------------------------------------------
  function spatialLabel(t, forEl) {
    // (1) Cisco explicit label markup: prefer a real label in the SAME
    // form-group/row ancestor over any geometric guess. Cisco renders field
    // labels as <span class="label">, <label>, or .pl-mandatory inside the
    // field's .form-group/.row. Use it when present (never blanks a label —
    // only overrides geometry when a real label exists).
    if (forEl) {
      const grp = forEl.closest('.form-group, .row');
      if (grp) {
        const lbl = grp.querySelector('span.label, label, .pl-mandatory');
        if (lbl && !lbl.querySelector('input, textarea, select, button')) {
          const lt = (lbl.innerText || lbl.textContent || '').trim();
          if (lt && lt.length <= 60) return lt;
        }
      }
    }
    const sel = 'label,span,div,a,p,td,th,h1,h2,h3,h4,h5,h6,strong,em';
    const cs = document.querySelectorAll(sel);
    let bestText = null;
    let bestScore = Infinity;
    const inputEnd = t.x + t.w;
    const inputCenterY = t.y + t.h / 2;
    for (const el of cs) {
      // Skip containers that wrap form elements.
      if (el.querySelector('input, textarea, select, button')) continue;
      // (2) Skip Kendo value/decoration chrome — the span that renders a
      // widget's SELECTED VALUE ('OSPF', '255.255.255.0'), never a label.
      if (el.closest('.k-input, .k-widget, .k-dropdown-wrap, .k-numerictextbox, .k-list, .k-grid')) continue;
      // Skip table headers and their descendants.
      if (el.tagName === 'TH' || el.tagName === 'THEAD') continue;
      if (el.closest('th, thead')) continue;
      const text = (el.innerText || el.textContent || '').trim();
      if (!text || text.length > 60) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;

      // Layout A: label ABOVE the input.
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
      const labelCenterY = r.y + r.height / 2;
      const dyCenter = Math.abs(inputCenterY - labelCenterY);
      const labelEnd2 = r.x + r.width;
      const dxGap = t.x - labelEnd2;
      if (dyCenter <= 20 && dxGap >= 0 && dxGap <= 200) {
        const score = dxGap + 5;
        if (score < bestScore) {
          bestScore = score;
          bestText = text;
        }
      }
    }
    return bestText || '';
  }

  // -----------------------------------------------------------------------
  // Kendo select name — ported from semantic_dom._resolve_kendo_select_name JS
  // -----------------------------------------------------------------------
  function kendoSelectName(listboxEl) {
    let node = listboxEl;
    for (let i = 0; i < 6; i++) {
      if (!node || !node.parentElement) break;
      node = node.parentElement;
      const sel = node.querySelector('select');
      if (sel) {
        const name = sel.getAttribute('name') || sel.getAttribute('id') || '';
        if (name) return name;
      }
    }
    return null;
  }

  // -----------------------------------------------------------------------
  // labelledby text helper
  // -----------------------------------------------------------------------
  function labelledbyText(el) {
    const ref = el.getAttribute('aria-labelledby');
    if (!ref) return '';
    const refEl = document.getElementById(ref.split(' ')[0]);
    if (!refEl) return '';
    return (refEl.innerText || refEl.textContent || '').trim();
  }

  // -----------------------------------------------------------------------
  // label[for] helper
  // -----------------------------------------------------------------------
  function labelForText(el) {
    const id = el.getAttribute('id');
    if (!id) return '';
    const lbl = document.querySelector('label[for="' + id + '"]');
    if (!lbl) return '';
    return (lbl.innerText || lbl.textContent || '').trim();
  }

  // -----------------------------------------------------------------------
  // options array — text of all <option> children
  // -----------------------------------------------------------------------
  function optionTexts(el) {
    const opts = el.querySelectorAll('option');
    const result = [];
    for (const o of opts) {
      const t = (o.innerText || o.textContent || '').trim();
      if (t) result.push(t);
    }
    return result;
  }

  // -----------------------------------------------------------------------
  // Main loop
  // -----------------------------------------------------------------------
  const elements = document.querySelectorAll(UNION_SEL);
  const MAX = 200;
  const descriptors = [];

  for (const el of elements) {
    if (descriptors.length >= MAX) break;
    if (!isVisible(el)) continue;

    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const cls = el.getAttribute('class') || '';
    const id = el.getAttribute('id') || '';
    const name_attr = el.getAttribute('name') || '';
    // How many elements on the page share this name? Cisco's Basic/Advanced
    // forms reuse one name (e.g. "processID") across a <select> + several
    // inputs, so [name=X] is NOT a unique handle when name_count > 1.
    const name_count = name_attr
      ? document.querySelectorAll('[name="' + name_attr + '"]').length
      : 0;
    const ng_model = el.getAttribute('ng-model') || '';
    const aria_label = el.getAttribute('aria-label') || '';
    const placeholder = el.getAttribute('placeholder') || '';
    const title = el.getAttribute('title') || '';
    const required = el.hasAttribute('required');
    // checked: only meaningful for checkbox/radio
    let checked = null;
    if (type === 'checkbox' || type === 'radio' || role === 'checkbox' || role === 'radio') {
      checked = el.checked !== undefined ? el.checked : null;
    }
    const aria_controls = el.getAttribute('aria-controls') || '';

    // Bounding box
    const r = el.getBoundingClientRect();
    const bbox = { x: r.x, y: r.y, w: r.width, h: r.height };

    // Spatial label (needs bbox; el lets it prefer Cisco label markup + skip
    // Kendo value chrome)
    const spatial = (r.width > 0 && r.height > 0) ? spatialLabel(bbox, el) : '';

    // labelledby text
    const lby_text = labelledbyText(el);

    // label[for] text
    const lfor_text = labelForText(el);

    // Ancestor checks
    const is_kendo_numeric = !!el.closest('.k-numerictextbox');
    const is_kendo_grid = !!el.closest('.k-grid');

    // Kendo select name (only relevant if role is listbox)
    let kendo_select_name = null;
    if (role === 'listbox') {
      kendo_select_name = kendoSelectName(el);
    }

    // Options: native <select> or backing kendo select
    let options = [];
    if (tag === 'select') {
      options = optionTexts(el);
    } else if (kendo_select_name) {
      // options will be resolved from the hidden select below
      const hiddenSel = el.closest('form, .k-widget, .k-dropdown-wrap, div, span')
        && document.querySelector('select[name="' + kendo_select_name + '"]');
      if (hiddenSel) {
        options = optionTexts(hiddenSel);
      }
    }

    // Visible text content (used for button labels like "Add", "Apply to Device")
    const inner_text = (el.innerText || el.textContent || '').trim().slice(0, 80);

    // Current value — for inputs/textareas use .value; for kendo, read backing select
    let value = '';
    if (tag === 'input' || tag === 'textarea') {
      value = el.value || '';
    } else if (kendo_select_name) {
      const hiddenSel2 = document.querySelector('select[name="' + kendo_select_name + '"]');
      if (hiddenSel2) {
        // Get the displayed text for the selected option
        const selIdx = hiddenSel2.selectedIndex;
        if (selIdx >= 0 && hiddenSel2.options[selIdx]) {
          value = hiddenSel2.options[selIdx].text || hiddenSel2.value || '';
        }
      }
    }

    descriptors.push({
      tag,
      type,
      role,
      aria_label,
      labelledby_text: lby_text,
      label_for_text: lfor_text,
      spatial_label: spatial,
      placeholder,
      title,
      name_attr,
      name_count,
      id,
      ng_model,
      classes: cls,
      kendo_select_name,
      options,
      aria_controls,
      required,
      checked,
      is_kendo_numeric,
      is_kendo_grid,
      bbox,
      inner_text,
      value
    });
  }
  return descriptors;
}
"""


# ---------------------------------------------------------------------------
# Pure functions — no page dependency
# ---------------------------------------------------------------------------


def classify_widget(desc: dict) -> str:
    """Classify a descriptor into a WIDGET_TYPES value.

    Precedence (first match wins):
    1. Apply-glyph classes → "button"
    2. tag button / role button / input[type in {submit,reset,button}] → "button"
    3. input[type=checkbox] or role checkbox → "checkbox"
    4. input[type=radio] or role radio → "radio"
    5. is_kendo_grid → "kendo_grid"
    6. is_kendo_numeric → "kendo_numeric"  (BEFORE generic input)
    7. role listbox/combobox OR tag select OR kendo_select_name present
       OR is_kendo_numeric==False but has options → "kendo_combobox"
    8. input[type=number] → "kendo_numeric"  (native number input fallback)
    9. textarea or input[text/email/tel/url/search/password] or role textbox → "input"
    10. else → "input"
    """
    tag = (desc.get("tag") or "").lower()
    itype = (desc.get("type") or "text").lower()
    role = (desc.get("role") or "").lower()
    kendo_select_name = desc.get("kendo_select_name")
    is_kendo_numeric = desc.get("is_kendo_numeric", False)
    is_kendo_grid = desc.get("is_kendo_grid", False)
    options = desc.get("options") or []

    # 1. Apply-glyph → button
    if is_apply_control(desc):
        return "button"

    # 2. Explicit button signals
    if (
        tag == "button"
        or role == "button"
        or (tag == "input" and itype in {"submit", "reset", "button"})
    ):
        return "button"

    # 3. Checkbox
    if (tag == "input" and itype == "checkbox") or role == "checkbox":
        return "checkbox"

    # 4. Radio
    if (tag == "input" and itype == "radio") or role == "radio":
        return "radio"

    # 5. Kendo grid
    if is_kendo_grid:
        return "kendo_grid"

    # 6. Kendo numeric (BEFORE generic input/combobox checks)
    if is_kendo_numeric:
        return "kendo_numeric"

    # 7. Combobox family
    if (
        role in {"listbox", "combobox"}
        or tag == "select"
        or kendo_select_name
        or (not is_kendo_numeric and options)
    ):
        return "kendo_combobox"

    # 8. Native number input (fallback for kendo_numeric when not inside k-numerictextbox)
    if tag == "input" and itype == "number":
        return "kendo_numeric"

    # 9. Text-family inputs + textbox role
    if tag == "textarea" or role == "textbox":
        return "input"
    if tag == "input" and itype in {"text", "email", "tel", "url", "search", "password", ""}:
        return "input"

    # 10. Catch-all
    return "input"


def resolve_label(desc: dict) -> str:
    """Resolve the human-readable label for an element descriptor.

    Resolution order (mirrors semantic_dom.describe_page):
      1. aria_label
      2. labelledby_text
      3. inner_text  — visible text content; catches button labels like "Add"
      4. label_for_text
      5. spatial_label
      6. placeholder
      7. title
      8. name_attr
      9. id (skip ng-* auto-generated Angular ids)

    Truncated to 80 characters.

    Defense-in-depth: a ``spatial_label`` that EXACTLY equals the descriptor's
    own current ``value`` is rejected — a label can never be the field's own
    value (a Kendo selected-value span leaking as a label, e.g. startingIp ->
    '255.255.255.0').  Such a spatial_label is skipped and the next source wins.
    """
    own_value = (desc.get("value") or "").strip()
    for key in (
        "aria_label",
        "labelledby_text",
        "inner_text",
        "label_for_text",
        "spatial_label",
        "placeholder",
        "title",
        "name_attr",
    ):
        val = (desc.get(key) or "").strip()
        if key in ("spatial_label", "inner_text") and val and own_value and val == own_value:
            # Label source equals the field's own value → not a real label. For a
            # Kendo combobox the visible widget's inner_text IS the selected value
            # (e.g. "255.255.255.0" / "IPV4"), never the field label — reject it so
            # the real label (form-group span.label) wins and the field isn't
            # keyed/named by its value.
            continue
        if val:
            return val[:80]

    id_val = (desc.get("id") or "").strip()
    if id_val and not id_val.startswith("ng-"):
        return id_val[:80]

    return ""


def resolve_key(desc: dict, label: str) -> str:
    """Derive a machine-readable field key from a descriptor + label.

    Resolution order:
      1. name_attr (stripped, lowercased) — ONLY when it is unique on the page
         (``name_count <= 1``).  A non-unique name (Cisco reuses "processID"
         across a <select> + input) is skipped so distinct controls get
         distinct keys from their unique ng_model tail instead of colliding.
      2. ng_model tail (part after last ".")
      3. slugify(label)
      4. "" (truly anonymous element)

    Always returns lowercase/normalized. Never empty if any of 1-3 yields a value.
    """
    name_attr = (desc.get("name_attr") or "").strip()
    name_count = desc.get("name_count") or 0
    if name_attr and name_count <= 1:
        return name_attr.lower()

    ng_model = (desc.get("ng_model") or "").strip()
    if ng_model:
        tail = ng_model.rsplit(".", 1)[-1]
        if tail:
            return tail.lower()

    # Non-unique name with no ng_model: fall back to the name (better than a
    # label slug for de-dup), even though it collides — the locator layer will
    # still narrow via _first_visible.
    if name_attr:
        return name_attr.lower()

    if label:
        sl = slugify(label)
        if sl:
            return sl

    return ""


def build_locator(desc: dict, role: str, label: str) -> LocatorSpec:
    """Build a LocatorSpec for the element.

    Primary strategy is the STABLE CSS selector (dom name/ng-model attribute),
    with get_by_role as fallback.  This order is critical: live Cisco DOM has
    stable name/ng-model attributes but get_by_role(label) fails on garbage
    labels resolved from junk text in the a11y tree.

    Priority:
      1. If ``name_attr`` → primary ``css [name='<name_attr>']``
      2. elif ``ng_model``  → primary ``css [ng-model='<ng_model>']``
      3. else               → primary ``get_by_role(role, name=label)``

    Fallbacks (appended in order, only when data exists):
      - get_by_role(role, name=label)  (when not already primary)
      - css [name='<name_attr>']       (when not already primary)
      - ng_model '<ng_model>'          (when not already primary)
      - for kendo: css ``select[name='<kendo_select_name>']``
    """
    name_attr = (desc.get("name_attr") or "").strip()
    name_count = desc.get("name_count") or 0
    ng_model = (desc.get("ng_model") or "").strip()
    kendo_select_name = desc.get("kendo_select_name")

    # A name shared by >1 element is not a single-element handle.  When that
    # happens AND we have a unique ng_model, prefer [ng-model=...] as primary
    # so locate() resolves exactly one element instead of 3-4 (the live
    # processid -> unknown_error fill failure).
    name_is_unique = name_count <= 1

    get_by_role_spec = LocatorSpec(strategy="get_by_role", role=role, name=label)
    css_name_spec = LocatorSpec(strategy="css", value=f"[name='{name_attr}']") if name_attr else None
    ng_model_css_spec = (
        LocatorSpec(strategy="css", value=f"[ng-model='{ng_model}']") if ng_model else None
    )
    ng_model_spec = LocatorSpec(strategy="ng_model", value=ng_model) if ng_model else None
    kendo_spec = (
        LocatorSpec(strategy="css", value=f"select[name='{kendo_select_name}']")
        if kendo_select_name
        else None
    )

    if name_attr and not name_is_unique and ng_model_css_spec is not None:
        # Non-unique name + unique ng-model → ng-model CSS is primary, name CSS
        # demoted to a fallback (still a usable handle after _first_visible).
        primary = ng_model_css_spec
        fallbacks: list[LocatorSpec] = [get_by_role_spec]
        if css_name_spec:
            fallbacks.append(css_name_spec)
    elif name_attr:
        # Primary: CSS by name attribute (unique, or non-unique with no ng-model)
        primary = css_name_spec
        fallbacks = [get_by_role_spec]
        if ng_model_spec:
            fallbacks.append(ng_model_spec)
    elif ng_model:
        # Primary: CSS by ng-model attribute
        primary = LocatorSpec(strategy="css", value=f"[ng-model='{ng_model}']")
        fallbacks = [get_by_role_spec]
        if css_name_spec:
            fallbacks.append(css_name_spec)
    else:
        # No stable DOM identity (buttons / links). Cisco's "Apply to Device"
        # button carries a save icon, so its accessible name is not an EXACT
        # match for the visible text — use lenient (substring) role matching,
        # then a text-based css fallback that also catches non-<button> ng-click
        # apply wrappers.
        primary = LocatorSpec(strategy="role_loose", role=role, name=label)
        fallbacks = [get_by_role_spec]
        if label and '"' not in label:
            fallbacks.append(
                LocatorSpec(
                    strategy="css",
                    value=f':is(button, a, [role="button"], [ng-click]):has-text("{label}")',
                )
            )
        if css_name_spec:
            fallbacks.append(css_name_spec)
        if ng_model_spec:
            fallbacks.append(ng_model_spec)

    if kendo_spec:
        fallbacks.append(kendo_spec)

    assert primary is not None
    primary.fallbacks = fallbacks
    return primary


def is_apply_control(desc: dict) -> bool:
    """Return True if the element carries an apply/save glyph.

    Checks the ``classes`` field for any of:
      - pl-save
      - icon-save-device
      - primaryActionButton
    """
    classes = desc.get("classes") or ""
    markers = (*_APPLY_ICON_CLASSES, _APPLY_PRIMARY_CLASS)
    return any(marker in classes for marker in markers)


def is_open_form_control(desc: dict) -> bool:
    """Return True if this is an 'Add / Add New / Create / New / +' button."""
    tag = (desc.get("tag") or "").lower()
    role = (desc.get("role") or "").lower()
    if tag != "button" and role != "button":
        return False
    label = resolve_label(desc).strip().lower()
    return label in _OPEN_FORM_LABELS


def _classify_role(desc: dict) -> str:
    """Derive an ARIA role from the descriptor.

    Explicit role attribute wins; else infer from tag + type.
    """
    role = (desc.get("role") or "").strip().lower()
    if role:
        return role

    tag = (desc.get("tag") or "").lower()
    if tag == "input":
        itype = (desc.get("type") or "text").lower()
        return _INPUT_TYPE_ROLE_MAP.get(itype, "textbox")

    return _TAG_ROLE_MAP.get(tag, "unknown")


def _has_stable_identity(desc: dict) -> bool:
    """Return True if the descriptor has a stable DOM identity (name/ng-model/kendo_select_name).

    Only descriptors with a stable identity become FieldSpec entries.  This gates
    out junk text cells, grid cells, version strings, and other elements that pass
    the form-control check but lack a reliable locator handle.
    """
    return bool(
        (desc.get("name_attr") or "").strip()
        or (desc.get("ng_model") or "").strip()
        or desc.get("kendo_select_name")
    )


def _build_field_spec(desc: dict, widget: str, role: str, label: str, key: str) -> FieldSpec:
    """Build a FieldSpec from a descriptor.  Shared by build_atlas and view_from_descriptors."""
    locator = build_locator(desc, role, label)
    kendo_select_name: str | None = desc.get("kendo_select_name")
    options: list[str] | None = None
    if widget == "kendo_combobox":
        raw_opts = desc.get("options")
        if raw_opts:
            options = [str(o) for o in raw_opts if str(o).strip()]
    return FieldSpec(
        key=key,
        label=label,
        role=role,
        widget=widget,
        required=bool(desc.get("required")),
        locator=locator,
        options=options,
        kendo_select_name=kendo_select_name if widget == "kendo_combobox" else None,
    )


def build_atlas(
    descriptors: list[dict],
    *,
    route: str,
    device_fingerprint: str,
    page_title: str,
    captured_by: str = "auto-capture",
) -> RouteAtlas:
    """Build a RouteAtlas from a list of element descriptors.

    - FORM_FIELD widgets → FieldSpec entries ONLY when the descriptor has a
      stable DOM identity (name_attr, ng_model, or kendo_select_name).  Junk
      text cells, version strings, and grid cells without kendo backing are
      excluded.
    - kendo_grid elements without kendo_select_name are NOT form fields.
    - Apply controls (is_apply_control) → apply_controls ControlSpec.
    - Open-form controls (is_open_form_control) → open_form_control ControlSpec
      (last open-form button wins; typically there is at most one).
    - Elements with empty key AND empty label are skipped.

    The success_signal is set to a sane default ``SuccessSignal("a11y_text", "success")``
    that can be refined by an operator override later.
    """
    fields: list[FieldSpec] = []
    field_keys_seen: set[str] = set()
    apply_controls: list[ControlSpec] = []
    open_form_control: ControlSpec | None = None

    for desc in descriptors:
        widget = classify_widget(desc)
        role = _classify_role(desc)
        label = resolve_label(desc)
        key = resolve_key(desc, label)

        # Skip truly anonymous elements.
        if not key and not label:
            continue

        if widget == "button":
            locator = build_locator(desc, role, label)
            ctrl = ControlSpec(
                key=key or slugify(label) or "btn",
                label=label,
                role=role,
                locator=locator,
            )
            if is_apply_control(desc):
                ctrl.is_router_write = True
                apply_controls.append(ctrl)
            elif is_open_form_control(desc):
                ctrl.reveals = "form"
                open_form_control = ctrl
            # Other buttons are not captured in the atlas (not form fields).
        elif widget in _FORM_FIELD_WIDGETS and _is_form_control(desc, role):
            # Identity gate: only real Cisco fields with a stable locator handle.
            # Any descriptor INSIDE a .k-grid that lacks a kendo backing select
            # is grid chrome (per-row dataItem.* select checkboxes, filter
            # widgets), not a fillable form field.  We gate on the captured
            # ``is_kendo_grid`` flag rather than ``widget == 'kendo_grid'``
            # because classify_widget types a row-select checkbox as "checkbox"
            # (rule 3 beats rule 5), so the widget check alone leaks it.
            if desc.get("is_kendo_grid") and not desc.get("kendo_select_name"):
                continue
            if not _has_stable_identity(desc):
                continue

            # De-dupe by key; first descriptor wins.
            if key and key in field_keys_seen:
                continue
            if key:
                field_keys_seen.add(key)

            fields.append(_build_field_spec(desc, widget, role, label, key))

    return RouteAtlas(
        route=route,
        device_fingerprint=device_fingerprint,
        page_title=page_title,
        fields=fields,
        apply_controls=apply_controls,
        open_form_control=open_form_control,
        success_signal=SuccessSignal("a11y_text", "success"),
        captured_at=datetime.now(UTC).isoformat(),
        captured_by=captured_by,
    )


def view_from_descriptors(
    descriptors: list[dict],
    *,
    route: str,
    device_fingerprint: str,
    page_title: str = "",
) -> dict:
    """Build the perceive VIEW directly from descriptors (DOM-keyed, no a11y reconcile).

    Returns a dict with the same structure as the reconcile view:
      {
        route, page_title, device_fingerprint,
        fields: [{key, label, role, widget, value, required, options}],
        apply_controls: [{key, label, role}],
        open_form_control: {key, label, role} | None,
        unmapped: [],
      }

    Field classification and identity gate are IDENTICAL to build_atlas so the
    two cannot drift.  ``value`` comes from each descriptor's ``value`` field
    (populated by _CAPTURE_JS from the live DOM).  Dedup by key (first wins).
    """
    fields: list[dict] = []
    field_keys_seen: set[str] = set()
    apply_controls: list[dict] = []
    open_form_control: dict | None = None

    for desc in descriptors:
        widget = classify_widget(desc)
        role = _classify_role(desc)
        label = resolve_label(desc)
        key = resolve_key(desc, label)

        if not key and not label:
            continue

        if widget == "button":
            ctrl_key = key or slugify(label) or "btn"
            ctrl = {"key": ctrl_key, "label": label, "role": role}
            if is_apply_control(desc):
                apply_controls.append(ctrl)
            elif is_open_form_control(desc):
                open_form_control = ctrl
        elif widget in _FORM_FIELD_WIDGETS and _is_form_control(desc, role):
            # Same identity gate as build_atlas (these two paths MUST NOT drift).
            # Drop any in-grid descriptor without a kendo backing select — this
            # covers the row-select checkbox classify_widget types as "checkbox".
            if desc.get("is_kendo_grid") and not desc.get("kendo_select_name"):
                continue
            if not _has_stable_identity(desc):
                continue

            if key and key in field_keys_seen:
                continue
            if key:
                field_keys_seen.add(key)

            raw_opts = desc.get("options")
            options: list[str] | None = None
            if widget == "kendo_combobox" and raw_opts:
                options = [str(o) for o in raw_opts if str(o).strip()]

            fields.append(
                {
                    "key": key,
                    "label": label,
                    "role": role,
                    "widget": widget,
                    "value": (desc.get("value") or ""),
                    "required": bool(desc.get("required")),
                    "options": options,
                }
            )

    return {
        "route": route,
        "page_title": page_title,
        "device_fingerprint": device_fingerprint,
        "fields": fields,
        "apply_controls": apply_controls,
        "open_form_control": open_form_control,
        # Mirror the SuccessSignal default build_atlas sets (a11y_text/"success").
        # The executor uses this as the post-apply verify target when the planner
        # supplies no verify_text, so a write is never marked clean unverified.
        "success_signal_contains": "success",
        "unmapped": [],
    }


# ---------------------------------------------------------------------------
# Page I/O
# ---------------------------------------------------------------------------


def extract_descriptors(page: Page) -> list[dict]:
    """Run the single batched DOM extraction JS and return raw descriptor dicts.

    This is the ONLY call to ``page.evaluate`` in the capture path.
    """
    result = page.evaluate(_CAPTURE_JS)
    if not isinstance(result, list):
        return []
    return result


def capture_route(
    page: Page,
    *,
    route: str,
    device_fingerprint: str,
    page_title: str | None = None,
    captured_by: str = "auto-capture",
) -> RouteAtlas:
    """Capture a RouteAtlas from the current page in one evaluate call.

    Does exactly:
      1. ``page.evaluate(_CAPTURE_JS)`` — one batched DOM read.
      2. ``page.title()`` — only if page_title is not supplied.
      3. Pure ``build_atlas(descriptors, ...)`` — no more page I/O.
    """
    descriptors = extract_descriptors(page)
    title = page_title if page_title is not None else page.title()
    atlas = build_atlas(
        descriptors,
        route=route,
        device_fingerprint=device_fingerprint,
        page_title=title,
        captured_by=captured_by,
    )
    logger.info(
        "atlas_capture_complete",
        route=route,
        field_count=len(atlas.fields),
        captured_by=captured_by,
    )
    return atlas
