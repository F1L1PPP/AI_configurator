"""Typed widget adapters for atlas-driven form interaction.

Each adapter knows how to LOCATE, APPLY a value to, and READ BACK one kind
of Cisco WebUI control.  The act-path rewrite dispatches to these by the
``FieldSpec.widget`` type.

EXCEPTION CONTRACT (load-bearing — the act-path self-heal depends on it):
- ``PlaywrightTimeoutError`` from any open/click/fill MUST propagate uncaught.
  The act path classifies it ``element_intercepted`` and retries once.
  NEVER swallow a timeout into a generic error.
- ``ValueError`` = a true dead-end (e.g. "value not in options") — propagates,
  NOT retried.
- Kendo widget-API JS errors (non-timeout) are caught and fall through to the
  next strategy (never become a generic error).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from backend.core.logging import get_logger

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

from backend.webui_agent.atlas.schema import FieldSpec, LocatorSpec

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Timeout constants (mirrors _playwright_subprocess._ACT_TIMEOUT_*)
# ---------------------------------------------------------------------------

CLICK_TIMEOUT_MS = 5000
FORM_TIMEOUT_MS = 4000

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class LocatorResolutionError(Exception):
    """Raised when an atlas field cannot be located on the page.

    The act path routes this failure to the vision rung for a screenshot-based
    fallback rather than retrying with the same atlas locator.
    """


# ---------------------------------------------------------------------------
# JS strategy strings (module-level constants — no per-call allocation)
# ---------------------------------------------------------------------------

_JS_WIDGET_API = """
(listboxEl, targetValue) => {
    // Guard: kendo global must exist.
    if (typeof kendo === 'undefined') {
        return {ok: false, reason: 'kendo_unavailable'};
    }
    // Find the Kendo widget wrapper.  Test the START element FIRST: Cisco's
    // visible span already carries k-widget/k-dropdown, so a parent-first walk
    // overshoots and kendo.widgetInstance lands on a non-widget ancestor.
    let wrapper = listboxEl;
    for (let i = 0; i < 8; i++) {
        if (wrapper && wrapper.classList && (
            wrapper.classList.contains('k-widget') ||
            wrapper.classList.contains('k-dropdown')
        )) break;
        if (!wrapper || !wrapper.parentElement) break;
        wrapper = wrapper.parentElement;
    }
    let widget;
    try {
        widget = kendo.widgetInstance(wrapper);
    } catch (e) {
        return {ok: false, reason: 'widget_instance_failed', detail: String(e)};
    }
    if (!widget || typeof widget.value !== 'function') {
        return {ok: false, reason: 'no_widget_instance'};
    }
    // Try setting the value FIRST — a diagnostics-collection error must never
    // abort the actual selection.
    widget.value(targetValue);
    const actual = widget.value();
    if (actual !== targetValue) {
        // Collect available options for error reporting (guarded — dataSource
        // .data() can be non-iterable on some widgets).
        let available = [];
        try {
            const dataSource = widget.dataSource;
            if (dataSource && typeof dataSource.data === 'function') {
                available = Array.from(dataSource.data() || []).map(
                    d => d.text || d.value || String(d)
                );
            }
        } catch (e) { available = []; }
        return {ok: false, reason: 'value_not_in_options', available: available};
    }
    widget.trigger('change');
    return {ok: true, selected: actual};
}
"""

# Shared JS picker: from the VISIBLE widget anchor, resolve the ONE backing
# <select> that widget owns. Cisco renders duplicate same-name selects
# (Basic/Advanced/template copies) so a global select[name=...] matches >1 and
# Playwright strict-mode-fails. querySelectorAll never throws; we then pick the
# select whose Kendo widget wrapper IS this anchor (deterministic), else the one
# in a visible container, else give up (never silently write a hidden copy).
_JS_PICK_FN = """
    function pickActiveSelect(anchorEl, selectName) {
        const cands = Array.from(document.querySelectorAll(
            "select[name='" + selectName + "'], select[id='" + selectName + "']"
        ));
        if (cands.length === 0) return null;
        if (cands.length === 1) return cands[0];
        const anchorWidget = (anchorEl && anchorEl.closest) ? anchorEl.closest('.k-widget') : null;
        // 1) the select whose Kendo widget wrapper IS this visible widget.
        for (const s of cands) {
            try {
                const w = window.jQuery && (window.jQuery(s).data('kendoDropDownList')
                    || window.jQuery(s).data('kendoComboBox'));
                const wrap = w && w.wrapper && w.wrapper[0];
                if (wrap && (wrap === anchorEl || wrap === anchorWidget
                        || wrap.contains(anchorEl) || (anchorEl && anchorEl.contains && anchorEl.contains(wrap)))) {
                    return s;
                }
            } catch (e) { /* keep looking */ }
        }
        // 2) the first whose ancestor container is rendered (the hidden
        //    Basic/template copy lives in a display:none / ng-hide container).
        //    Start the walk at parentElement, NOT the <select>: Kendo always
        //    renders the backing select itself display:none, so testing the
        //    select would reject every candidate on iteration 0. The container
        //    is what reflects the active-vs-hidden state.
        for (const s of cands) {
            let n = s.parentElement, vis = true;
            for (let i = 0; i < 12 && n; i++) {
                const cs = window.getComputedStyle(n);
                if (cs && (cs.display === 'none' || cs.visibility === 'hidden')) { vis = false; break; }
                if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') { vis = false; break; }
                if (n.classList && n.classList.contains('ng-hide')) { vis = false; break; }
                n = n.parentElement;
            }
            if (vis) return s;
        }
        // 3) give up rather than silently writing a hidden copy.
        return null;
    }
"""

_JS_SELECT_FROM_WIDGET = (
    "(anchorEl, arg) => {"
    + _JS_PICK_FN
    + """
    const selectName = arg.selectName;
    const targetValue = arg.value;
    if (!selectName) return {ok: false, error: 'no_kendo_select_name'};
    const selectEl = pickActiveSelect(anchorEl, selectName);
    if (!selectEl) {
        return {ok: false, error: 'no active backing <select> for name ' + selectName};
    }
    // Match the option by value OR visible text, case-insensitive and trimmed
    // (the planner passes display text like "255.255.255.128" while the option
    // value may be "25"; or "IPv4" vs value "ipv4").
    const tv = String(targetValue).trim().toLowerCase();
    let matched = null;
    for (const opt of selectEl.options) {
        if (opt.value.trim().toLowerCase() === tv || opt.text.trim().toLowerCase() === tv) {
            matched = opt;
            break;
        }
    }
    if (!matched) {
        const available = Array.from(selectEl.options).map(o => o.text).join(', ');
        return {ok: false, error: 'value not in options. available: ' + available};
    }
    selectEl.value = matched.value;
    // Native events for AngularJS ng-model / input watchers.
    selectEl.dispatchEvent(new Event('change', {bubbles: true}));
    selectEl.dispatchEvent(new Event('input', {bubbles: true}));
    // Drive the Kendo widget bound to THIS <select> so its visible value +
    // dataSource update — a raw select mutation alone can be re-synced away by
    // Kendo on the next interaction (the silent-wrong-value risk).
    let widgetVal = null;
    try {
        if (typeof kendo !== 'undefined') {
            let w = null;
            if (window.jQuery) {
                w = window.jQuery(selectEl).data('kendoDropDownList')
                    || window.jQuery(selectEl).data('kendoComboBox') || null;
            }
            if (!w) {
                try { w = kendo.widgetInstance(selectEl); } catch (e) { w = null; }
            }
            if (w && typeof w.value === 'function') {
                w.value(matched.value);
                if (typeof w.trigger === 'function') w.trigger('change');
                widgetVal = w.value();
            }
        }
    } catch (e) {
        widgetVal = '__widget_err__';
    }
    // AngularJS 1.x digest so ng-model commits before the Apply submit.
    try {
        if (window.angular) {
            const scope = window.angular.element(selectEl).scope();
            if (scope && typeof scope.$applyAsync === 'function') scope.$applyAsync();
        }
    } catch (e) { /* best-effort */ }
    // Verify the value actually took (no silent-wrong-value): the native select
    // must hold the chosen option AND the Kendo widget (if resolved) must agree.
    const finalVal = selectEl.value;
    const ok = finalVal === matched.value
        && (widgetVal === null || widgetVal === '__widget_err__'
            || String(widgetVal) === String(matched.value));
    return {
        ok: ok,
        selected: finalVal,
        widget_value: widgetVal,
        matched_value: matched.value,
        select_name: selectEl.getAttribute('name') || selectEl.getAttribute('id') || '(unnamed)',
        select_id: selectEl.getAttribute('id') || '(none)',
        candidate_count: document.querySelectorAll(
            "select[name='" + selectName + "'], select[id='" + selectName + "']"
        ).length
    };
}"""
)

# Read variant: resolve the same active <select> and return its current value
# (the value attr, matching the prior read_back semantics).
_JS_READ_FROM_WIDGET = (
    "(anchorEl, selectName) => {"
    + _JS_PICK_FN
    + """
    if (!selectName) return null;
    const selectEl = pickActiveSelect(anchorEl, selectName);
    return selectEl ? selectEl.value : null;
}"""
)

# ---------------------------------------------------------------------------
# Locator resolution helpers
# ---------------------------------------------------------------------------


def resolve_locator(page: Page, locspec: LocatorSpec) -> Locator:
    """Build ONE Playwright locator from a single LocatorSpec by strategy.

    Pure helper — just builds the locator object, no I/O.
    """
    strategy = locspec.strategy
    if strategy == "get_by_role":
        return page.get_by_role(locspec.role, name=locspec.name, exact=True)  # type: ignore[arg-type]
    if strategy == "role_loose":
        # Lenient (substring, case-insensitive) accessible-name match. Cisco's
        # "Apply to Device" button carries a save icon, so its accessible name
        # is not an exact match for the visible text — exact=True misses it.
        return page.get_by_role(locspec.role, name=locspec.name, exact=False)  # type: ignore[arg-type]
    if strategy == "css":
        return page.locator(locspec.value)
    if strategy == "ng_model":
        return page.locator(f"[ng-model='{locspec.value}']")
    if strategy == "name":
        return page.locator(f"[name='{locspec.value}']")
    # Unknown strategy — fall back to CSS-like treatment.
    return page.locator(locspec.value or "")


def _first_visible(loc: Locator) -> Locator:
    """Narrow a possibly-multi-match locator to a single element.

    Cisco renders Basic + Advanced (and grid-template) copies of a form, so a
    ``[name='X']`` selector can match several elements — the live OSPF form has
    ``name='processID'`` 4 times. ``fill()`` then raises a strict-mode violation
    ("resolved to N elements"). Return the first VISIBLE match (the active form
    field); if none report visible, fall back to ``.first`` so acting still has
    a single handle.
    """
    try:
        cnt = loc.count()
    except Exception:  # noqa: BLE001
        return loc
    if cnt <= 1:
        return loc
    for i in range(cnt):
        nth = loc.nth(i)
        try:
            if nth.is_visible():
                return nth
        except Exception:  # noqa: BLE001
            continue
    return loc.first


def locate(page: Page, field: FieldSpec) -> Locator:
    """Resolve a FieldSpec to a live Playwright Locator.

    Tries the primary locator first, then each fallback in order.  Returns the
    first whose ``.count() > 0``, narrowed to a single VISIBLE element (Cisco
    duplicates field names across Basic/Advanced sections). A malformed selector
    (count() raises) falls through to the next fallback rather than crashing.

    Raises ``LocatorResolutionError(field.key)`` when no locator resolves.
    """
    if field.locator is None:
        raise LocatorResolutionError(field.key)

    candidates: list[LocatorSpec] = [field.locator, *field.locator.fallbacks]
    for locspec in candidates:
        try:
            loc = resolve_locator(page, locspec)
            if loc.count() > 0:
                return _first_visible(loc)
        except Exception:  # noqa: BLE001
            # Malformed selector or other structural error — try next fallback.
            continue

    raise LocatorResolutionError(field.key)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WidgetAdapter(Protocol):
    """Protocol for all widget adapters."""

    widget: str  # class-level discriminator key

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:
        """Write *value* to the field on *page*."""
        ...

    def read_back(self, page: Page, field: FieldSpec) -> str | bool | None:
        """Read the current value of the field from *page*."""
        ...


# ---------------------------------------------------------------------------
# Concrete adapters
# ---------------------------------------------------------------------------


class InputAdapter:
    """Plain HTML ``<input>`` / ``<textarea>`` text field."""

    widget = "input"

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:
        loc = locate(page, field)
        loc.fill(str(value or ""), timeout=FORM_TIMEOUT_MS)

    def read_back(self, page: Page, field: FieldSpec) -> str | None:
        loc = locate(page, field)
        return loc.input_value(timeout=FORM_TIMEOUT_MS)


class KendoNumericAdapter:
    """Kendo numeric text box (editable input inside ``.k-numerictextbox``)."""

    widget = "kendo_numeric"

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:
        loc = locate(page, field)
        loc.fill(str(value or ""), timeout=FORM_TIMEOUT_MS)

    def read_back(self, page: Page, field: FieldSpec) -> str | None:
        loc = locate(page, field)
        return loc.input_value(timeout=FORM_TIMEOUT_MS)


class CheckboxAdapter:
    """Native ``<input type='checkbox'>`` or ``role='checkbox'`` element."""

    widget = "checkbox"

    _TRUTHY: frozenset[Any] = frozenset({True, "true", "1", "yes", "on"})

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:
        loc = locate(page, field)
        checked = value in self._TRUTHY
        loc.set_checked(checked, timeout=FORM_TIMEOUT_MS)

    def read_back(self, page: Page, field: FieldSpec) -> bool:
        loc = locate(page, field)
        return loc.is_checked(timeout=FORM_TIMEOUT_MS)


class RadioAdapter:
    """Native ``<input type='radio'>`` or ``role='radio'`` element."""

    widget = "radio"

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:
        loc = locate(page, field)
        loc.check(timeout=FORM_TIMEOUT_MS)

    def read_back(self, page: Page, field: FieldSpec) -> bool:
        loc = locate(page, field)
        return loc.is_checked(timeout=FORM_TIMEOUT_MS)


class ButtonAdapter:
    """Clickable button / submit control.  ``value`` is ignored on apply."""

    widget = "button"

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:  # noqa: ARG002
        loc = locate(page, field)
        loc.click(timeout=CLICK_TIMEOUT_MS)

    def read_back(self, page: Page, field: FieldSpec) -> None:  # noqa: ARG002
        return None


class KendoComboboxAdapter:
    """Kendo UI dropdown / combobox.

    Three strategies are tried in order:

    1. Widget JS API — ``kendo.widgetInstance(wrapper).value(target)``.
       Cleanest path; skipped when the ``kendo`` global is absent. Matches by
       VALUE only, so it misses options whose value differs from the display
       text (e.g. a subnet mask shown "255.255.255.128" with value "25").
    2. Backing ``<select>`` from the widget — pass the VISIBLE widget element as
       anchor and ``querySelectorAll``-pick the ONE backing ``<select>`` that
       widget owns (the select whose Kendo wrapper IS this anchor, else a
       visible-container one), match by value OR display text, set it, drive the
       Kendo widget + AngularJS model, and verify the value took. No popup →
       cannot be intercepted; the robust path for non-default values. (Evolved
       over the 2026-06-06 DHCP /25 smoke: a 6-level DOM walk missed the
       ``display:none`` select; a global ``select[name=...]`` then strict-mode-failed
       because Cisco renders duplicate same-name selects — so we resolve from the
       widget anchor instead.)
    3. Real DOM (LAST RESORT) — click to open the popup, then click the matching
       ``li.k-item`` (scoped by ``aria-controls``/``aria-owns`` when available;
       else body-wide ``ul.k-list li.k-item``). Interception-prone; only runs
       when the backing select can't satisfy the request.

    EXCEPTION CONTRACT:
    - ``PlaywrightTimeoutError`` → always propagates (never swallowed); from the
      last-resort DOM click the act path classifies it as ``element_intercepted``.
    - ``ValueError`` → dead-end (all strategies exhausted) → propagates, NOT retried.
    - JS errors that are not timeouts → caught, log, fall through to next strategy.
    """

    widget = "kendo_combobox"

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

        target_value = str(value or "")
        loc = locate(page, field)

        # -------------------------------------------------------------------
        # Strategy 1 — Kendo widget JS API
        # -------------------------------------------------------------------
        try:
            result = loc.evaluate(_JS_WIDGET_API, target_value)
            if isinstance(result, dict) and result.get("ok"):
                logger.info(
                    "kendo_select_success",
                    strategy="widget_api",
                    selected=result.get("selected"),
                    requested_value=target_value,
                )
                return
            # Strategy 1 matches by VALUE only; a value-not-in-options here is
            # NOT a hard dead-end (strategy 2/3 may match by display text).
            logger.info(
                "kendo_select_strategy1_unavailable",
                reason=result.get("reason") if isinstance(result, dict) else repr(result),
                available=result.get("available") if isinstance(result, dict) else None,
                requested_value=target_value,
            )
        except PlaywrightTimeoutError:
            raise  # propagate — act path classifies as element_intercepted
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "kendo_select_strategy1_error",
                error=str(exc),
                error_type=type(exc).__name__,
                requested_value=target_value,
            )

        # -------------------------------------------------------------------
        # Strategy 2 — Backing <select> resolved FROM the visible widget anchor.
        # The robust path for non-default values. Cisco renders duplicate
        # same-name <select>s (Basic/Advanced/template), so a global
        # select[name=...] matches >1 and Playwright strict-mode-fails (which is
        # what knocked this strategy out and dropped us to the popup click). We
        # pass the VISIBLE widget `loc` (single element, narrowed by
        # _first_visible) as the anchor and let the JS querySelectorAll-pick the
        # ONE select that widget owns, then set + drive Kendo/AngularJS + verify.
        # No popup → cannot be intercepted. Skipped (→ Strategy 3) when no name.
        # -------------------------------------------------------------------
        if field.kendo_select_name:
            try:
                result2 = loc.evaluate(
                    _JS_SELECT_FROM_WIDGET,
                    {"value": target_value, "selectName": field.kendo_select_name},
                )
                if isinstance(result2, dict) and result2.get("ok"):
                    logger.info(
                        "kendo_select_success",
                        strategy="hidden_select",
                        select_name=result2.get("select_name"),
                        select_id=result2.get("select_id"),
                        candidate_count=result2.get("candidate_count"),
                        selected=result2.get("selected"),
                        widget_value=result2.get("widget_value"),
                        requested_value=target_value,
                    )
                    return
                logger.info(
                    "kendo_select_strategy_hidden_unavailable",
                    reason=(result2.get("error") if isinstance(result2, dict) else repr(result2)),
                    widget_value=(
                        result2.get("widget_value") if isinstance(result2, dict) else None
                    ),
                    requested_value=target_value,
                )
            except PlaywrightTimeoutError:
                raise  # propagate
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "kendo_select_strategy_hidden_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    requested_value=target_value,
                )
        else:
            logger.info(
                "kendo_select_strategy_hidden_unavailable",
                reason="no_kendo_select_name",
                requested_value=target_value,
            )

        # -------------------------------------------------------------------
        # Strategy 3 — Real DOM popup click (LAST RESORT; interception-prone).
        # Only reached when the backing select couldn't satisfy the request.
        # -------------------------------------------------------------------
        try:
            # Guard: skip the open-click if the widget is already expanded.
            try:
                aria_expanded = loc.get_attribute("aria-expanded", timeout=FORM_TIMEOUT_MS)
                already_open = isinstance(aria_expanded, str) and aria_expanded.lower() == "true"
            except Exception:  # noqa: BLE001
                already_open = False

            if not already_open:
                loc.click(timeout=FORM_TIMEOUT_MS)

            # Resolve the popup list.  Prefer aria-controls / aria-owns scoping
            # over the body-wide fallback — avoids cross-widget pollution when
            # multiple dropdowns are open.
            controls_id: str | None = None
            try:
                controls_id = loc.get_attribute("aria-controls", timeout=FORM_TIMEOUT_MS)
                if not controls_id:
                    controls_id = loc.get_attribute("aria-owns", timeout=FORM_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                controls_id = None

            _scoped_clicked = False
            if controls_id:
                # Use the attribute form ``[id="..."]`` — a Kendo popup id is
                # often a GUID starting with a digit, which is an INVALID CSS id
                # selector (``#334a...``) and throws, abandoning the scoping.
                list_loc = page.locator(f'[id="{controls_id}"]')
                try:
                    list_loc.locator("li.k-item", has_text=target_value).first.click(
                        timeout=FORM_TIMEOUT_MS
                    )
                    _scoped_clicked = True
                except PlaywrightTimeoutError:
                    raise  # propagate — bounded retry classification
                except Exception as exc:  # noqa: BLE001
                    # Scoped click failed structurally (bad id, detached) — fall
                    # through to the body-wide path.
                    logger.info(
                        "kendo_select_scoped_click_fell_through",
                        error=str(exc),
                        controls_id=controls_id,
                        requested_value=target_value,
                    )

            if not _scoped_clicked:
                page.locator("ul.k-list li.k-item", has_text=target_value).first.click(
                    timeout=FORM_TIMEOUT_MS
                )

            logger.info(
                "kendo_select_success",
                strategy="dom_click",
                selected=target_value,
                requested_value=target_value,
            )
            return
        except PlaywrightTimeoutError:
            raise  # propagate — last resort; act path classifies as element_intercepted
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"kendo_select failed (all strategies): {exc}") from exc

    def read_back(self, page: Page, field: FieldSpec) -> str | None:
        if field.kendo_select_name:
            # Resolve the SAME active backing <select> apply() targets — via the
            # visible widget anchor + querySelectorAll-pick. A global
            # select[name=...] strict-mode-fails on Cisco's duplicate same-name
            # selects (that throw used to be silently swallowed by the
            # idempotent-skip's suppress, so read-back never actually ran).
            loc = locate(page, field)
            return loc.evaluate(_JS_READ_FROM_WIDGET, field.kendo_select_name)
        loc = locate(page, field)
        # Bounded timeout — a bare inner_text() would use Playwright's 30 s
        # default and reintroduce the stalls this rebuild exists to remove.
        return loc.inner_text(timeout=FORM_TIMEOUT_MS)


class KendoGridAdapter:
    """Kendo grid widget — minimal stub; not on the OSPF/DHCP critical path."""

    widget = "kendo_grid"

    def apply(self, page: Page, field: FieldSpec, value: Any) -> None:  # noqa: ARG002
        raise NotImplementedError("kendo_grid apply not on the OSPF/DHCP critical path")

    def read_back(self, page: Page, field: FieldSpec) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTER_INSTANCES: list[Any] = [
    InputAdapter(),
    KendoNumericAdapter(),
    CheckboxAdapter(),
    RadioAdapter(),
    ButtonAdapter(),
    KendoComboboxAdapter(),
    KendoGridAdapter(),
]

ADAPTERS: dict[str, Any] = {a.widget: a for a in _ADAPTER_INSTANCES}

_DEFAULT_ADAPTER = InputAdapter()


def get_adapter(widget: str) -> Any:
    """Return the adapter for *widget*, defaulting to ``InputAdapter`` for unknowns."""
    return ADAPTERS.get(widget, _DEFAULT_ADAPTER)
