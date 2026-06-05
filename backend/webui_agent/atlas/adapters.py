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
    // Walk up to the Kendo widget wrapper (k-widget or k-dropdown-wrap).
    let wrapper = listboxEl;
    for (let i = 0; i < 8; i++) {
        if (!wrapper || !wrapper.parentElement) break;
        wrapper = wrapper.parentElement;
        if (wrapper.classList && (
            wrapper.classList.contains('k-widget') ||
            wrapper.classList.contains('k-dropdown')
        )) break;
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
    // Collect available options for error reporting.
    const dataSource = widget.dataSource;
    let available = [];
    if (dataSource && typeof dataSource.data === 'function') {
        available = dataSource.data().map(d => d.text || d.value || String(d));
    }
    // Try setting the value.
    widget.value(targetValue);
    const actual = widget.value();
    if (actual !== targetValue) {
        return {ok: false, reason: 'value_not_in_options', available: available};
    }
    widget.trigger('change');
    return {ok: true, selected: actual};
}
"""

_JS_HIDDEN_SELECT = """
(listboxEl, targetValue) => {
    // Walk up the DOM to find a hidden <select> in the same Kendo wrapper.
    let node = listboxEl;
    let select = null;
    for (let i = 0; i < 6; i++) {
        if (!node || !node.parentElement) break;
        node = node.parentElement;
        select = node.querySelector('select');
        if (select) break;
    }
    if (!select) {
        return {ok: false, error: 'backing select not found (walked 6 levels)'};
    }
    // Find the matching option by value or visible text, case-insensitive
    // and trimmed (the planner may pass "IPv4" while the option text is
    // "IPV4" and the value is "ipv4").
    let found = false;
    const tv = String(targetValue).trim().toLowerCase();
    for (const opt of select.options) {
        if (opt.value.trim().toLowerCase() === tv || opt.text.trim().toLowerCase() === tv) {
            select.value = opt.value;
            found = true;
            break;
        }
    }
    if (!found) {
        const available = Array.from(select.options).map(o => o.text).join(', ');
        return {ok: false, error: 'value not in options. available: ' + available};
    }
    // Dispatch change event so Kendo/AngularJS model updates.
    select.dispatchEvent(new Event('change', {bubbles: true}));
    // Also dispatch input event for Angular 1.x watchers.
    select.dispatchEvent(new Event('input', {bubbles: true}));
    const selectName = select.getAttribute('name') || select.getAttribute('id') || '(unnamed)';
    return {ok: true, selected: select.value, select_name: selectName};
}
"""

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

    Three strategies are tried in order — ported verbatim from
    ``_playwright_subprocess._kendo_select``:

    1. Widget JS API — ``kendo.widgetInstance(wrapper).value(target)``.
       Cleanest path; skipped when the ``kendo`` global is absent.
    2. Real DOM — click to open popup, then click the matching ``li.k-item``.
       Scoped to the listbox identified by ``aria-controls``/``aria-owns``
       when available; falls back to body-wide ``ul.k-list li.k-item``.
    3. Hidden-select — walk up to the backing ``<select>`` and set its value
       case-insensitively, then dispatch ``change``/``input`` events.

    EXCEPTION CONTRACT (mirrors _kendo_select exactly):
    - ``PlaywrightTimeoutError`` → always propagates (never swallowed).
    - ``ValueError`` → dead-end (value not in options / evaluate failure) →
      propagates, NOT retried.
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
        # Strategy 2 — Real DOM via Playwright
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

            if controls_id:
                list_loc = page.locator(f"#{controls_id}")
                list_loc.locator("li.k-item", has_text=target_value).first.click(
                    timeout=FORM_TIMEOUT_MS
                )
            else:
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
            raise  # propagate
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "kendo_select_strategy2_error",
                error=str(exc),
                error_type=type(exc).__name__,
                requested_value=target_value,
            )

        # -------------------------------------------------------------------
        # Strategy 3 — Hidden-select + change/input dispatch
        # -------------------------------------------------------------------
        try:
            result3 = loc.evaluate(_JS_HIDDEN_SELECT, target_value)
        except PlaywrightTimeoutError:
            raise  # propagate
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"kendo_select failed (all strategies): evaluate error: {exc}"
            ) from exc

        if not isinstance(result3, dict) or not result3.get("ok"):
            error_detail = (
                result3.get("error", "unknown") if isinstance(result3, dict) else repr(result3)
            )
            raise ValueError(f"kendo_select failed (all strategies): {error_detail}")

        logger.info(
            "kendo_select_success",
            strategy="hidden_select",
            select_name=result3.get("select_name"),
            selected=result3.get("selected"),
            requested_value=target_value,
        )

    def read_back(self, page: Page, field: FieldSpec) -> str | None:
        if field.kendo_select_name:
            loc = page.locator(f"select[name='{field.kendo_select_name}']")
            return loc.input_value(timeout=FORM_TIMEOUT_MS)
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
