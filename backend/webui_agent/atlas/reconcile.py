"""Atlas reconcile step — match live accessibility nodes against the atlas.

This is the hot-path perceive step: given the atlas for a route and the
live ``page.accessibility.snapshot()`` tree, produce a structured view
with matched field values (using ATLAS labels, never live node names).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.webui_agent.atlas.schema import FieldSpec, RouteAtlas

# Roles considered "interactive" during DFS flattening.
INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "textbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "button",
        "link",
        "menuitem",
        "spinbutton",
        "slider",
    }
)

# Roles that count as "form fields" for drift detection.
# buttons / links / menuitems do NOT count as unexpected form fields.
_FORM_ROLES: frozenset[str] = frozenset(
    {"textbox", "combobox", "listbox", "checkbox", "radio", "spinbutton"}
)

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Flatten
# ---------------------------------------------------------------------------


def flatten_interactive(snapshot: dict | None) -> list[dict]:
    """Depth-first walk of an accessibility snapshot tree.

    Returns a flat list of ``{"role", "name", "value", "checked"}`` dicts
    for nodes whose ``role`` is in :data:`INTERACTIVE_ROLES`.

    Order is DFS pre-order.  Tolerates ``None`` snapshot and missing children.
    """
    if snapshot is None:
        return []

    result: list[dict] = []
    _dfs(snapshot, result)
    return result


def _dfs(node: dict, result: list[dict]) -> None:
    role = node.get("role", "")
    if role in INTERACTIVE_ROLES:
        result.append(
            {
                "role": role,
                "name": node.get("name", ""),
                "value": node.get("value", ""),
                "checked": node.get("checked"),
            }
        )
    for child in node.get("children") or []:
        _dfs(child, result)


# ---------------------------------------------------------------------------
# Normalise / equivalence
# ---------------------------------------------------------------------------


def normalize_name(s: str | None) -> str:
    """Normalise a UI label for fuzzy comparison.

    Steps:
      1. Trim leading/trailing whitespace.
      2. Strip a single trailing ``*`` (required marker).
      3. Strip a trailing ``:``.
      4. Collapse internal whitespace runs to a single space.
      5. ``casefold()``.
    """
    if not s:
        return ""
    t = s.strip()
    if t.endswith("*"):
        t = t[:-1]
    if t.endswith(":"):
        t = t[:-1]
    t = _WS_RE.sub(" ", t).strip()
    return t.casefold()


def roles_equivalent(a: str, b: str) -> bool:
    """Return True if roles *a* and *b* should be treated as the same widget.

    Exact match OR the Kendo-dropdown case where the visible span surfaces as
    ``listbox`` but the atlas records it as ``combobox``.
    """
    if a == b:
        return True
    return {a, b} == {"combobox", "listbox"}


# ---------------------------------------------------------------------------
# ReconcileResult
# ---------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    """Output of :func:`reconcile`."""

    view: dict
    drift: bool
    missing_required: list[str]
    extra_live: list[dict]
    unmapped_fields: list[str]


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


def reconcile(atlas: RouteAtlas, live_nodes: list[dict]) -> ReconcileResult:
    """Match atlas fields against live accessibility nodes.

    Returns a :class:`ReconcileResult` with:

    * ``view`` — structured page view for the planner.
    * ``drift`` — True when required fields are missing or unexpected
      form fields appear (indicates re-capture may be needed).
    * ``missing_required`` — keys of required atlas fields with no live match.
    * ``extra_live`` — live form-role nodes not consumed by any atlas field.
    * ``unmapped_fields`` — ALL atlas field keys with no live match (superset
      of missing_required).
    """
    consumed: set[int] = set()  # indices into live_nodes

    matched: list[tuple[FieldSpec, dict]] = []  # (FieldSpec, live_node)
    unmatched_fields = []

    # --- Pass 1: name+role match ---
    for fs in atlas.fields:
        norm_label = normalize_name(fs.label)
        found_idx: int | None = None
        for idx, node in enumerate(live_nodes):
            if idx in consumed:
                continue
            if roles_equivalent(fs.role, node.get("role", "")) and normalize_name(
                node.get("name")
            ) == norm_label:
                found_idx = idx
                break
        if found_idx is not None:
            consumed.add(found_idx)
            matched.append((fs, live_nodes[found_idx]))
        else:
            unmatched_fields.append(fs)

    # --- Pass 2: Kendo value-named fallback ---
    # Condition: exactly one unmatched combobox-family atlas field AND
    #            exactly one unconsumed combobox/listbox live node.
    _COMBO_FAMILY = {"combobox", "listbox"}
    unmatched_combo_fields = [
        fs for fs in unmatched_fields if fs.role in _COMBO_FAMILY
    ]
    unconsumed_combo_nodes = [
        (idx, node)
        for idx, node in enumerate(live_nodes)
        if idx not in consumed and node.get("role", "") in _COMBO_FAMILY
    ]

    if len(unmatched_combo_fields) == 1 and len(unconsumed_combo_nodes) == 1:
        fs = unmatched_combo_fields[0]
        idx, node = unconsumed_combo_nodes[0]
        consumed.add(idx)
        matched.append((fs, node))
        unmatched_fields = [f for f in unmatched_fields if f is not fs]

    # --- Build view ---
    view_fields = []
    for fs, node in matched:
        view_fields.append(
            {
                "key": fs.key,
                "label": fs.label,  # ATLAS label always wins
                "role": fs.role,
                "widget": fs.widget,
                "value": node.get("value", ""),
                "required": fs.required,
                "options": fs.options,
            }
        )

    view_apply = [
        {"key": c.key, "label": c.label, "role": c.role} for c in atlas.apply_controls
    ]

    unmapped_keys = [fs.key for fs in unmatched_fields]
    # Surface the open-form control (e.g. the "Add"/"Create" button on a list
    # page) so the orchestrator can click it to reveal the form before planning.
    # capture stores it on the atlas but it is NOT a field, so without this it
    # would be invisible to the propose flow (form never opens → empty plan).
    open_form = atlas.open_form_control
    view = {
        "route": atlas.route,
        "page_title": atlas.page_title,
        "fields": view_fields,
        "apply_controls": view_apply,
        "unmapped": unmapped_keys,
        "open_form_control": (
            {"key": open_form.key, "label": open_form.label, "role": open_form.role}
            if open_form is not None
            else None
        ),
    }

    # --- Drift detection ---
    missing_required = [fs.key for fs in unmatched_fields if fs.required]
    extra_live = [
        {"role": node.get("role", ""), "name": node.get("name", "")}
        for idx, node in enumerate(live_nodes)
        if idx not in consumed and node.get("role", "") in _FORM_ROLES
    ]
    drift = bool(missing_required) or bool(extra_live)

    return ReconcileResult(
        view=view,
        drift=drift,
        missing_required=missing_required,
        extra_live=extra_live,
        unmapped_fields=unmapped_keys,
    )
