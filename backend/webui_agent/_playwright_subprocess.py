"""Isolated child-process entrypoint for the Playwright portion of a WebUI flow.

Why this exists
---------------
Windows + Playwright sync API + FastAPI's thread pool has a Catch-22 in
asyncio:

  - WindowsProactorEventLoop  → has subprocess_exec, missing add_reader
  - WindowsSelectorEventLoop  → has add_reader, missing subprocess_exec

Playwright sync API needs BOTH on Windows (subprocess_exec to launch the
Chromium driver, then pipe handling to talk to it). When the calling
thread is part of a FastAPI process, no single asyncio policy lets
Playwright start up cleanly.

The cleanest fix is to give Playwright its own fresh Python process.
This module is that process. It supports TWO entry shapes:

1. **One-shot mode** (existing, used by `flows/change_hostname.py` and
   `flows/add_access_vlan.py`): the parent passes `{"flow": "<name>",
   "args": {...}}` on stdin, the child runs that one flow and exits.
   Used for the hand-coded fast paths.

2. **Session mode** (Phase 4): the parent passes
   `{"mode": "session", "action_id": "...", "headless": null}` on stdin
   followed by additional JSON-line ops (`open` / `describe` / `verify`
   / `act` / `shutdown`). The child logs in once and stays alive across
   ops, holding the Playwright Page + the latest `describe_page`
   locator_map in local memory. Used by the AI-driven generic driver in
   `generic_driver.py`. Lifetime = one planner turn; 120 s watchdog
   backstop via the parent.

Invocation (one-shot):
    proc = subprocess.run(
        [sys.executable, "-m", "backend.webui_agent._playwright_subprocess"],
        input=json.dumps({"flow": "add_access_vlan", "args": {...}}),
        capture_output=True, text=True, timeout=...
    )

Invocation (session):
    proc = subprocess.Popen([sys.executable, "-m", "backend.webui_agent._playwright_subprocess"],
                            stdin=PIPE, stdout=PIPE, stderr=DEVNULL, text=True, bufsize=1)
    proc.stdin.write(json.dumps({"mode": "session", "action_id": ...}) + "\n")
    proc.stdin.flush()
    ready = json.loads(proc.stdout.readline())  # {"ok": true, "ready": true, ...}
    # then per-op:
    proc.stdin.write(json.dumps({"op": "open", "path": "/webui/#/general"}) + "\n")
    proc.stdin.flush()
    reply = json.loads(proc.stdout.readline())

Protocol (one-shot):
  - stdin:  `{"flow": "<name>", "args": {<kwargs>}}`
  - stdout: `{"ok": true,  "result": {...}}` on success
            `{"ok": false, "error": "<msg>", "exc_type": "<name>"}` on failure
            (also exit code 1 on failure, traceback on stderr).

Protocol (session): JSON-line on stdin, JSON-line replies on stdout, one
reply per op. Each reply is `{"ok": bool, ...}` with op-specific fields.
The child writes to stdout flushed after every reply so the parent's
blocking `readline()` returns immediately.

The parent does pre-snapshot, post-snapshot, CLI verify, state
transitions, and pool invalidation. Splitting the responsibilities
this way keeps confirmations / Netmiko state in a single process
(the parent) while Playwright lives wherever its asyncio quirks are
satisfied (the child).
"""

from __future__ import annotations

import contextlib
import json
import sys
import traceback
from collections.abc import Callable
from typing import Any

# C3: module-level import so tests can patch
# ``backend.webui_agent._playwright_subprocess.get_adapter``.
# The import itself is deferred to avoid paying the Playwright/atlas import
# cost on one-shot child startup — we shadow it here so the name exists at
# module scope for monkeypatching.
try:
    from backend.webui_agent.atlas.adapters import get_adapter  # noqa: F401
except Exception:  # noqa: BLE001 — child process may lack settings on import
    get_adapter = None  # noqa: BLE001 — defensive; the real import succeeded above


def _do_add_access_vlan(args: dict[str, Any]) -> dict[str, Any]:
    """Playwright-only steps for the add-access-VLAN flow.

    Mirrors the inner `with webui_browser(...)` block of
    flows/add_access_vlan.py — login + form + save + screenshots — but
    nothing else. Returns the evidence-session dir path so the parent
    can include it in its response.
    """
    # Imports are inside the function so module discovery (e.g.
    # `python -m backend.webui_agent._playwright_subprocess --help`)
    # doesn't pay the playwright import cost. Also keeps the child's
    # surface area minimal until the flow actually runs.
    from backend.webui_agent.browser import webui_browser
    from backend.webui_agent.evidence import EvidenceCollector
    from backend.webui_agent.login import login
    from backend.webui_agent.pages.vlan_page import VlanPage

    vlan_id = int(args["vlan_id"])
    vlan_name = str(args["vlan_name"])
    action_id = str(args["action_id"])
    headless = args.get("headless")

    ev = EvidenceCollector("add_access_vlan", action_id=action_id)
    page = None
    try:
        with webui_browser(headless=headless) as page:
            ev.step("01-browser-launched", page)
            if not login(page):
                ev.dump_dom(page, "99-login-failed")
                raise RuntimeError("WebUI login failed")
            ev.step("02-logged-in", page)

            vp = VlanPage(page)
            vp.goto()
            ev.step("03-vlan-page", page)

            vp.click_add()
            ev.step("04-add-form-opened", page)

            vp.set_vlan_id(vlan_id)
            vp.set_vlan_name(vlan_name)
            ev.step("05-form-filled", page)

            vp.save()
            ev.step("06-saved", page)

        return {"screenshots": str(ev.session_dir)}
    except Exception:
        # Dump DOM for forensics if we have a page reference, then let
        # the outer main() format the failure for the parent.
        if page is not None:
            with contextlib.suppress(Exception):
                ev.dump_dom(page, "99-error")
        raise


def _do_change_hostname(args: dict[str, Any]) -> dict[str, Any]:
    """Playwright-only steps for the change-hostname flow.

    Returns `old_hostname` (read off the form before Apply) + the
    evidence-session dir — the parent stitches both into the tool
    response.
    """
    from backend.webui_agent.browser import webui_browser
    from backend.webui_agent.evidence import EvidenceCollector
    from backend.webui_agent.login import login
    from backend.webui_agent.pages.hostname_page import HostnamePage

    new_name = str(args["new_name"])
    action_id = str(args["action_id"])
    headless = args.get("headless")

    ev = EvidenceCollector("change_hostname", action_id=action_id)
    page = None
    try:
        with webui_browser(headless=headless) as page:
            ev.step("01-browser-launched", page)
            if not login(page):
                ev.dump_dom(page, "99-login-failed")
                raise RuntimeError("WebUI login failed")
            ev.step("02-logged-in", page)

            hp = HostnamePage(page)
            hp.goto()
            ev.step("03-hostname-form", page)

            old_hostname = hp.get_current_hostname()

            hp.set_hostname(new_name)
            ev.step("04-form-filled", page)

            hp.apply()
            ev.step("05-applied", page)

        return {"old_hostname": old_hostname, "screenshots": str(ev.session_dir)}
    except Exception:
        if page is not None:
            with contextlib.suppress(Exception):
                ev.dump_dom(page, "99-error")
        raise


# Dispatch table — add new flow handlers here as they're built. The
# parent-side caller of `run_flow_in_subprocess` must pass a flow name
# that appears as a key here, otherwise the subprocess exits with a
# clear "unknown flow" error.
_DISPATCH: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "add_access_vlan": _do_add_access_vlan,
    "change_hostname": _do_change_hostname,
}


# ---------------------------------------------------------------------------
# Session mode (Phase 4) — long-lived loop for AI-driven WebUI configuration.
# ---------------------------------------------------------------------------


def _reply(payload: dict[str, Any]) -> None:
    """Write one JSON-line reply on stdout, flushed.

    Flushing matters: the parent does a blocking `readline()`; without
    flush the reply can sit in the OS pipe buffer indefinitely. On
    Windows specifically (Python bug #34504) this would deadlock.
    """
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _resolve_target_url(page: Any, raw_path: str) -> str:
    """Turn a relative path (e.g. ``/webui/#/general``) into an absolute URL.

    Playwright's ``page.goto`` requires an absolute URL. The existing
    HostnamePage / VlanPage do this by splitting ``page.url`` on
    ``/webui/`` and prepending the base. Mirror that here so the
    session-mode ``open`` op accepts both absolute and relative inputs.

    If ``raw_path`` already has a scheme, return it unchanged. Otherwise
    derive ``scheme://host`` from the current page URL.
    """
    if raw_path.startswith(("http://", "https://")):
        from urllib.parse import urlparse  # noqa: PLC0415

        from backend.core.settings import get_settings  # noqa: PLC0415

        parsed = urlparse(raw_path)
        settings = get_settings()
        # Prefer the explicit router_host field if present; fall back to
        # parsing the hostname from router_webui_base_url.
        expected_host = getattr(settings, "router_host", None)
        if not expected_host:
            expected_host = urlparse(settings.router_webui_base_url).hostname
        if not parsed.hostname or parsed.hostname != expected_host:
            raise RuntimeError(
                f"_resolve_target_url refused absolute URL with host "
                f"{parsed.hostname!r} (expected {expected_host!r})"
            )
        return raw_path

    current = page.url or ""
    # Preferred path: split on /webui/ like the existing pages do.
    if "/webui/" in current:
        base = current.split("/webui/")[0]
        return f"{base}{raw_path}"

    # Fallback: derive scheme+host from page.url via urllib.
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(current)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{raw_path}"

    # Last resort: load the base URL from settings.
    from backend.core.settings import get_settings  # noqa: PLC0415

    base = get_settings().router_webui_base_url.rstrip("/")
    return f"{base}{raw_path}"


def _relogin_if_needed(page: Any) -> None:
    """Re-run WebUI login if the page has dropped back to the login screen.

    The Cisco WebUI session idles out after ~5 minutes. The planner can
    pause that long between ops (waiting on Claude, waiting on Filip),
    so the next op would land on the login page instead of the requested
    feature. URL-based heuristic: `login.is_session_expired()` doesn't
    exist (verified by the Phase 4 slice 2 mapping pass), so we check
    `"login" in page.url.lower()` instead and re-run `login(page)`.
    """
    if "login" in page.url.lower():
        # Lazy import — keeps cold start cheap for the one-shot path.
        from backend.webui_agent.login import login

        login(page)


# Actions accepted by `webui_act`. Anything else returns failure_reason="unknown_action".
_VALID_ACTIONS = frozenset({"click", "fill", "select", "check", "hover"})

# Accessible-name substrings that _do_act_by_intent must NEVER act on.
# Defends against prompt-injected intents that resolve to destructive controls.
_SENSITIVE_DENY_LIST = frozenset(
    {
        "factory reset",
        "reboot",
        "restart",
        "delete user",
        "restore configuration",
        "disable http server",
        "clear configuration",
    }
)

# Cancel-resolution guard (plan #2 — destructive "Cancel" click).
#
# On the DHCP Create Pool form the "Apply to Device" submit control historically
# went un-surfaced, so the planner's "Apply to Device" click mis-resolved (via
# vision/heuristics) onto the nearby "Cancel" button — which reset the form and
# drove the fill→cancel→refill loop. Even with the control now surfaced (plan #1),
# the resolver must NEVER satisfy a submit/apply intent by landing on a
# Cancel/Close element: doing so silently discards the user's form input.
#
# _APPLY_INTENT_TOKENS — if the INTENT name contains one of these, the resolution
# is a submit/apply. _CANCEL_NAME_TOKENS — if the RESOLVED element's accessible
# name contains one of these, it is a cancel/close control. The intersection is
# refused (returns unknown_eid). Matched on word boundaries so "ok" does not fire
# on "Lookup" and "close" does not fire on "disclosure".
_APPLY_INTENT_TOKENS = frozenset({"apply", "save", "submit", "ok"})
_CANCEL_NAME_TOKENS = frozenset({"cancel", "close"})


def _is_apply_intent(intent_name: str) -> bool:
    """True if the planner intent name denotes a submit/apply/save/ok action.

    Tokenised on non-alphanumeric boundaries so a substring like the "ok" in
    "Lookup" or "Bookmark" never counts — only a standalone token matches.
    Returns False for non-str input (defensive — callers may pass a probe result).
    """
    import re  # noqa: PLC0415

    if not isinstance(intent_name, str):
        return False
    tokens = {t for t in re.split(r"[^a-z0-9]+", intent_name.lower()) if t}
    return bool(tokens & _APPLY_INTENT_TOKENS)


def _is_cancel_control_name(accessible_name: str) -> bool:
    """True if a resolved element's accessible name denotes Cancel/Close.

    Token-boundary match (see ``_is_apply_intent``) so "close" does not fire on
    "disclosure" / "closed-loop". Returns False for non-str input — the
    accessible name comes from a best-effort Playwright probe that may be absent.
    """
    import re  # noqa: PLC0415

    if not isinstance(accessible_name, str):
        return False
    tokens = {t for t in re.split(r"[^a-z0-9]+", accessible_name.lower()) if t}
    return bool(tokens & _CANCEL_NAME_TOKENS)

# Per-action Playwright timeout (ms).
#
# Click keeps a 5 s budget: it fires an XHR that may have already landed
# at the router before Playwright sees the timeout — we must not retry it,
# so a slightly longer window reduces false positives.
#
# Fill / select / check / hover use a 4 s budget: these are read-modify
# operations on form fields. An absent or intercepted field should fail
# fast so the planner sees the error quickly and the convergence guard
# (tool_registry.py) can abort rather than burning 50+ s per iteration.
_ACT_TIMEOUT_CLICK_MS = 5000
_ACT_TIMEOUT_FORM_MS = 4000

# Legacy alias — external code that imports this constant keeps working.
# Internally _invoke_action now selects the right budget per action.
_ACT_TIMEOUT_MS = _ACT_TIMEOUT_CLICK_MS

# Settle budget after a successful action — Playwright tries networkidle first
# (covers Cisco's chatty Angular XHR bursts), and if the page never reaches
# idle within the window, falls back to a small fixed sleep (covers pages with
# polling timers that prevent networkidle from ever firing).
_SETTLE_NETWORKIDLE_MS = 800
_SETTLE_FALLBACK_MS = 250


def _settle_page(page: Any) -> None:
    """Wait for the page to stabilise after a click/fill/etc.

    The ISIS Add form surfaced a race: a click opens a modal, the modal
    renders asynchronously, and Cisco's Angular dismiss-on-blur sometimes
    closes it before describe_page snapshots the elements. By the time we
    iterate locators in describe_page, the modal can be gone — and the
    inner LLM sees a blank view and returns empty plan.

    Strategy:
      1. Try ``page.wait_for_load_state("networkidle", timeout=800)`` —
         Cisco's Angular modals usually finish their open animation when
         network is quiet. Fast-out path: most healthy pages return in
         well under 800ms.
      2. If networkidle never fires (some Cisco pages have polling timers
         that keep the network busy indefinitely), sleep 250ms as a
         fixed fallback. That's enough for the dismiss-on-blur fade
         (~200ms) — the full enter animation is slower (~300ms) but if a
         modal doesn't open in time the next describe will fail loudly
         rather than silently.

    Cost: ~250-800ms per successful action. On a 5-step static-route
    flow that's ~1.25-4s extra — acceptable given the correctness win.
    Best-effort: any exception is swallowed; we still try to describe
    and let the planner decide.
    """
    import time as _time  # noqa: PLC0415

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

    try:
        page.wait_for_load_state("networkidle", timeout=_SETTLE_NETWORKIDLE_MS)
    except PlaywrightTimeoutError:
        # Page never reached networkidle in the window — fall back to a
        # fixed sleep for animation completion.
        _time.sleep(_SETTLE_FALLBACK_MS / 1000.0)
    except Exception:  # noqa: BLE001
        # Anything else (page closed, navigation in flight, etc.) — swallow
        # and let the describe_page that follows surface a real error.
        pass


def _describe_with_retry(page: Any, max_attempts: int = 2) -> tuple[dict[str, Any], dict[str, Any]]:
    """Describe the page; on empty result, settle and retry up to max_attempts.

    Cisco Angular pages can return an empty view if describe runs before
    AngularJS finishes mounting controllers. Most pages settle in <500ms
    after domcontentloaded, but the DHCP and OSPF detail forms have been
    seen empty on the first call. Retry once after a fresh settle.

    Returns whatever the last attempt produced — caller decides if an
    empty view is still an error.
    """
    from backend.webui_agent.semantic_dom import describe_page  # noqa: PLC0415

    for attempt_idx in range(max_attempts):
        view, locator_map = describe_page(page)
        elements = view.get("elements") or []
        modals = view.get("modals") or []
        if elements or modals:
            return view, locator_map
        if attempt_idx + 1 < max_attempts:
            _settle_page(page)
    return view, locator_map


def _is_kendo_listbox(locator: Any) -> bool:
    """Return True if ``locator`` points at a Kendo UI listbox widget.

    Kendo dropdowns render a visible ``<span role='listbox'>`` (the clickable
    widget) backed by a HIDDEN ``<select>`` in the same container. Calling
    ``select_option()`` on the visible span does nothing — Kendo ignores DOM
    mutations on the visible widget; only the hidden select + a ``change``
    event updates the Kendo model.

    Fast-path: a single ``get_attribute("role")`` call with a short timeout.
    Best-effort: returns False on any exception so the caller falls back to
    the standard ``select_option`` path.
    """
    try:
        role = locator.get_attribute("role", timeout=_ACT_TIMEOUT_FORM_MS)
        return isinstance(role, str) and role.strip().lower() == "listbox"
    except Exception:  # noqa: BLE001
        return False


def _kendo_select(locator: Any, value: str) -> None:
    """Drive a Kendo UI dropdown to select ``value``.

    Kendo wraps a hidden ``<select>`` with a visible ``<span role='listbox'>``
    widget. Calling ``select_option()`` on the visible span has no effect on
    the Kendo model. Three strategies are tried in order:

      1. Kendo widget JS API (kendo.widgetInstance + .value() + .trigger("change"))
         — cleanest path; skipped if the global kendo/$ object is unavailable.
      2. Real DOM via Playwright — click to open popup, click the list item.
         PlaywrightTimeoutError PROPAGATES (not caught) so the _do_act self-heal
         loop classifies it as element_intercepted and retries once.
      3. Hidden-select + change dispatch (original vanilla-JS path) — final fallback.

    EXCEPTION CONTRACT (matches _do_act self-heal loop):
    - PlaywrightTimeoutError  → propagates uncaught → classified element_intercepted → retried once.
    - ValueError              → dead-end (value not in option set) → not retried.
    - Widget-API JS errors    → caught; fall through to next strategy (never become unknown_error).

    Logs the winning strategy with log.info("kendo_select_success", strategy=...).
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

    from backend.core.logging import get_logger  # noqa: PLC0415

    _log = get_logger(__name__)
    page = locator.page

    # -----------------------------------------------------------------------
    # Strategy 1 — Kendo widget JS API.
    # Guard: typeof kendo !== 'undefined'. Walks up to the .k-widget/.k-dropdown
    # wrapper, calls kendo.widgetInstance(wrapper).value(target) then .trigger("change").
    # Returns a structured dict: {ok, reason?, selected?, available?}.
    # On ok=true → log + return.
    # On any non-ok result (kendo_unavailable / value_not_in_options / JS error)
    #   → fall through to strategy 2. Strategy 1 matches by VALUE only; the
    #   authoritative text-or-value dead-end check is at the end of strategy 3.
    # -----------------------------------------------------------------------
    js_widget_api = """
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
    try:
        result = locator.evaluate(js_widget_api, value)
        if isinstance(result, dict) and result.get("ok"):
            _log.info(
                "kendo_select_success",
                strategy="widget_api",
                selected=result.get("selected"),
                requested_value=value,
            )
            return
        # Strategy 1 matches by VALUE only (widget.value(target)). The
        # authoritative text-OR-value check lives in strategy 3, so a value-only
        # "not in options" here is NOT a dead-end — the planner may pass display
        # text that strategy 2's has_text click (or strategy 3's text match)
        # resolves. Fall through; the only hard ValueError is at end of strategy 3.
        _log.info(
            "kendo_select_strategy1_unavailable",
            reason=result.get("reason") if isinstance(result, dict) else repr(result),
            available=result.get("available") if isinstance(result, dict) else None,
            requested_value=value,
        )
    except PlaywrightTimeoutError:
        # Transient stall in the evaluate call itself → propagate so _do_act
        # classifies it as element_intercepted and retries once.
        raise
    except Exception as exc:  # noqa: BLE001
        # Any other JS/browser error — log and fall through to strategy 2.
        _log.info(
            "kendo_select_strategy1_error",
            error=str(exc),
            error_type=type(exc).__name__,
            requested_value=value,
        )

    # -----------------------------------------------------------------------
    # Strategy 2 — Real DOM via Playwright.
    # CLAUDE.md §4 compliance: selecting a list item from the Kendo popup is a
    # UI interaction on a form field, NOT the "Apply to Device" XHR click. It is
    # safe to re-click/re-open across a retry; only the final "Apply" click must
    # remain single-attempt.
    #
    # Live-DOM caveats (partial — do not over-engineer; smoke validates):
    #   - The popup <ul> may be body-level, possibly id="<select_name>_listbox"
    #     or reachable via aria-owns / aria-controls on the visible widget.
    #   - Re-opening an already-open widget on a retry can toggle it shut if
    #     aria-expanded is already "true". Guard cheaply if detectable.
    # PlaywrightTimeoutError PROPAGATES — this is intentional; _do_act self-heal
    # catches it and classifies element_intercepted for a bounded retry.
    # -----------------------------------------------------------------------
    try:
        # Guard: if widget is already open (aria-expanded="true"), skip the open click.
        try:
            aria_expanded = locator.get_attribute("aria-expanded", timeout=_ACT_TIMEOUT_FORM_MS)
            already_open = isinstance(aria_expanded, str) and aria_expanded.lower() == "true"
        except Exception:  # noqa: BLE001
            already_open = False

        if not already_open:
            locator.click(timeout=_ACT_TIMEOUT_FORM_MS)

        # Click the matching list item. The popup may be body-level (Kendo appends
        # the <ul role="listbox"> to <body>); the has_text filter narrows it.
        page.locator("ul.k-list li.k-item", has_text=value).click(
            timeout=_ACT_TIMEOUT_FORM_MS
        )
        _log.info(
            "kendo_select_success",
            strategy="dom_click",
            selected=value,
            requested_value=value,
        )
        return
    except PlaywrightTimeoutError:
        # Propagate — this is the intended classification path (element_intercepted).
        raise
    except Exception as exc:  # noqa: BLE001
        # Structural non-timeout errors (element detached, selector error, etc.) —
        # fall through to strategy 3; these are not retriable by _do_act anyway.
        _log.info(
            "kendo_select_strategy2_error",
            error=str(exc),
            error_type=type(exc).__name__,
            requested_value=value,
        )

    # -----------------------------------------------------------------------
    # Strategy 3 — Hidden-select + change/input dispatch (original path).
    # This is the vanilla-JS approach that was the sole implementation before
    # chunk 1. Kept verbatim as the last-resort fallback.
    # -----------------------------------------------------------------------
    js_hidden_select = """
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
    try:
        result3 = locator.evaluate(js_hidden_select, value)
    except PlaywrightTimeoutError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"kendo_select failed (all strategies): evaluate error: {exc}") from exc

    if not isinstance(result3, dict) or not result3.get("ok"):
        error_detail = (
            result3.get("error", "unknown") if isinstance(result3, dict) else repr(result3)
        )
        raise ValueError(f"kendo_select failed (all strategies): {error_detail}")

    _log.info(
        "kendo_select_success",
        strategy="hidden_select",
        select_name=result3.get("select_name"),
        selected=result3.get("selected"),
        requested_value=value,
    )


def _invoke_action(locator: Any, action: str, value: str | None) -> None:
    """Dispatch one Playwright action against ``locator``.

    Raises whatever Playwright raises (TimeoutError on intercepted / hidden /
    detached elements). Caller distinguishes via `isinstance` against
    `playwright.sync_api.TimeoutError`.

    Click uses ``_ACT_TIMEOUT_CLICK_MS`` (5 s): it fires an XHR that may
    have already reached the router before Playwright reports a timeout, so
    a slightly longer budget reduces false positives on congested WebUIs.

    All form actions (fill / select / check / hover) use ``_ACT_TIMEOUT_FORM_MS``
    (4 s): absent or intercepted form fields should fail fast so the planner
    convergence guard can abort rather than burning >50 s per iteration.

    **Kendo UI dropdowns**: if the locator points to a ``<span role='listbox'>``
    (Kendo's visible widget), ``select_option()`` has no effect on the Kendo
    model. We detect this and route through ``_kendo_select()`` which writes
    to the backing hidden ``<select>`` and dispatches the change event that
    Kendo/AngularJS listen for. Plain ``<select>`` handling is unchanged.
    """
    if action == "click":
        locator.click(timeout=_ACT_TIMEOUT_CLICK_MS)
    elif action == "fill":
        locator.fill(str(value or ""), timeout=_ACT_TIMEOUT_FORM_MS)
    elif action == "select":
        if _is_kendo_listbox(locator):
            # Kendo UI dropdown: hidden <select> + change event dispatch.
            _kendo_select(locator, str(value or ""))
        else:
            # Plain <select> — standard Playwright path. Unchanged.
            locator.select_option(str(value or ""), timeout=_ACT_TIMEOUT_FORM_MS)
    elif action == "check":
        locator.check(timeout=_ACT_TIMEOUT_FORM_MS)
    elif action == "hover":
        locator.hover(timeout=_ACT_TIMEOUT_FORM_MS)
    else:  # pragma: no cover — caller pre-validates against _VALID_ACTIONS
        raise ValueError(f"unknown action: {action!r}")


def _do_act(
    page: Any,
    locator_map: dict[str, Any],
    current_view_id: str | None,
    msg: dict[str, Any],
    ev: Any,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Run one act op with the self-heal state machine.

    Returns a tuple of (reply, new_locator_map, new_view_id). The caller
    rebinds the loop's state from the returned values — every code path
    here ensures the returned map and view_id reflect what the planner
    will see next.

    Failure modes:
    - `stale_view`         : the parent's view_id no longer matches.
    - `unknown_eid`        : eid not in the current locator_map.
    - `unknown_action`     : action not in _VALID_ACTIONS.
    - `click_timeout_unsafe_retry`: PlaywrightTimeoutError on a click. NEVER
        retried — CLAUDE.md §4 (Cisco WebUI Apply fires via XHR; the click
        may have already landed at the router).
    - `element_missing`    : non-click timeout, eid gone after re-describe.
    - `element_hidden`     : non-click timeout, element no longer visible.
    - `element_disabled`   : non-click timeout, element not enabled.
    - `element_intercepted`: non-click timeout, element looks fine — likely
        a modal/overlay. Retried once after re-describing.
    - `unknown_error`      : non-Playwright exception during the action.
    """
    # Lazy imports — keep cold start cheap.
    import contextlib as _contextlib  # noqa: PLC0415

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

    from backend.webui_agent.semantic_dom import _safe_bool, describe_page  # noqa: PLC0415

    # ---- 1. view_id staleness check ----
    if msg.get("view_id") != current_view_id:
        view, new_map = describe_page(page)
        return (
            {
                "ok": False,
                "failure_reason": "stale_view",
                "view": view,
                "attempts": 0,
            },
            new_map,
            view["view_id"],
        )

    eid = str(msg.get("eid", ""))
    action = str(msg.get("action", ""))
    value = msg.get("value")

    # ---- 2. action validation ----
    if action not in _VALID_ACTIONS:
        view, new_map = describe_page(page)
        return (
            {
                "ok": False,
                "failure_reason": "unknown_action",
                "view": view,
                "attempts": 0,
            },
            new_map,
            view["view_id"],
        )

    # ---- 3. eid lookup ----
    locator = locator_map.get(eid)
    if locator is None:
        view, new_map = describe_page(page)
        return (
            {
                "ok": False,
                "failure_reason": "unknown_eid",
                "view": view,
                "attempts": 0,
            },
            new_map,
            view["view_id"],
        )

    # ---- 4. Action dispatch with bounded self-heal ----
    # MAX_ATTEMPTS=2 means 1 initial attempt + 1 retry. Retry is ONLY for
    # non-click actions on TimeoutError after re-describe + element-still-OK.
    max_attempts = 2
    for attempt_idx in range(max_attempts):
        try:
            _invoke_action(locator, action, value)

            # Settle: networkidle (≤800ms) then 250ms fallback. Critical for
            # Cisco's Angular modals that render/dismiss faster than
            # describe_page can iterate locators. ISIS Add form was the
            # canonical case — modal opened then auto-dismissed before
            # the post-action describe captured it.
            _settle_page(page)

            # Success — re-describe so the planner sees the post-action view.
            ev.step(f"act-{eid}-{action}", page)
            view, new_map = describe_page(page)
            return (
                {
                    "ok": True,
                    "view": view,
                    "attempts": attempt_idx,
                    "evidence": {"screenshot_dir": str(ev.session_dir)},
                },
                new_map,
                view["view_id"],
            )

        except PlaywrightTimeoutError:  # noqa: PERF203
            # THE CRITICAL GUARD: click never retries on TimeoutError.
            # Cisco WebUI Apply clicks fire via XHR; a TimeoutError after
            # locator.click() may mean the network call already landed at
            # the router. Retrying = duplicate router write. CLAUDE.md §4.
            if action == "click":
                with _contextlib.suppress(Exception):
                    ev.dump_dom(page, f"99-click-timeout-{eid}")
                view, new_map = describe_page(page)
                return (
                    {
                        "ok": False,
                        "failure_reason": "click_timeout_unsafe_retry",
                        "view": view,
                        "attempts": attempt_idx,
                    },
                    new_map,
                    view["view_id"],
                )

            # Non-click: re-describe and classify what went wrong.
            view, locator_map = describe_page(page)
            current_view_id = view["view_id"]
            new_loc = locator_map.get(eid)

            if new_loc is None:
                return (
                    {
                        "ok": False,
                        "failure_reason": "element_missing",
                        "view": view,
                        "attempts": attempt_idx,
                    },
                    locator_map,
                    current_view_id,
                )

            visible = _safe_bool(new_loc.is_visible, default=False)
            if not visible:
                return (
                    {
                        "ok": False,
                        "failure_reason": "element_hidden",
                        "view": view,
                        "attempts": attempt_idx,
                    },
                    locator_map,
                    current_view_id,
                )

            enabled = _safe_bool(new_loc.is_enabled, default=False)
            if not enabled:
                return (
                    {
                        "ok": False,
                        "failure_reason": "element_disabled",
                        "view": view,
                        "attempts": attempt_idx,
                    },
                    locator_map,
                    current_view_id,
                )

            # Element looks fine but the action timed out — likely intercepted.
            # Retry once with the refreshed locator handle.
            if attempt_idx + 1 < max_attempts:
                locator = new_loc
                continue

            return (
                {
                    "ok": False,
                    "failure_reason": "element_intercepted",
                    "view": view,
                    "attempts": attempt_idx,
                },
                locator_map,
                current_view_id,
            )

        except Exception as exc:  # noqa: BLE001
            # Non-Playwright exception — fail loudly with the raw type.
            with _contextlib.suppress(Exception):
                ev.dump_dom(page, f"99-act-error-{eid}")
            view, new_map = describe_page(page)
            return (
                {
                    "ok": False,
                    "failure_reason": "unknown_error",
                    "view": view,
                    "attempts": attempt_idx,
                    "error": str(exc) or repr(exc),
                    "exc_type": type(exc).__name__,
                },
                new_map,
                view["view_id"],
            )

    # Should be unreachable: every branch above returns.
    view, new_map = describe_page(page)  # pragma: no cover
    return (  # pragma: no cover
        {
            "ok": False,
            "failure_reason": "retry_exhausted",
            "view": view,
            "attempts": max_attempts,
        },
        new_map,
        view["view_id"],
    )


def _eid_for_intent(view: dict[str, Any], role: str, name: str) -> str | None:
    """Forward-lookup: find the eid in ``view`` whose role AND name match the
    intent exactly. Returns the best eid or None if no match.

    Critical for Phase 3.4 spatial labels: describe_page invents a name
    for inputs without ARIA labels by reading the visible text above them.
    Playwright's own ``get_by_role(role, name=...)`` doesn't know about
    that spatial association, so it would either miss the textbox or
    (worse) fall through to a same-named link/header. By scanning the
    describe view we use the SAME naming source the inner LLM saw at
    propose time, so collisions like

        {role: textbox, name: "Prefix", required: true}   # the form input
        {role: link,    name: "Prefix"}                   # the column header

    resolve to the textbox when the planner asks for ``{role: textbox}``.

    Tie-breaking when multiple elements share role+name:
      1. Prefer ``required=True`` (form inputs that MUST be filled).
      2. Prefer ``enabled=True`` (filter out disabled mirrors).
      3. Otherwise return the first hit (stable across rebuilds).

    When the exact match yields no candidates, a conservative field_key bridge
    is attempted (see ``_normalize_for_field_key``).  The bridge only fires
    when the exact path returns nothing — it cannot override an exact match.
    """
    if not isinstance(role, str) or not isinstance(name, str):
        return None

    all_elements = list(view.get("elements") or []) + list(view.get("modals") or [])

    candidates = [
        el
        for el in all_elements
        if el.get("role") == role and el.get("name") == name and isinstance(el.get("eid"), str)
    ]
    if not candidates:
        # Conservative field_key bridge: only fires on exact-match MISS.
        # Requires same role + non-empty field_key + normalised prefix match.
        # Minimum intent length 4 prevents short tokens like "ip" or "id"
        # from bridging accidentally.
        bridge_result = _eid_for_intent_field_key_bridge(all_elements, role, name)
        if bridge_result is not None:
            return bridge_result
        return None

    if len(candidates) == 1:
        return candidates[0]["eid"]
    # Tie-break — required first, then enabled, then first.
    required = [c for c in candidates if c.get("required") is True]
    if required:
        return required[0]["eid"]
    enabled = [c for c in candidates if c.get("enabled") is True]
    if enabled:
        return enabled[0]["eid"]
    return candidates[0]["eid"]


def _normalize_for_field_key(s: str) -> str:
    """Lowercase and strip every non-alphanumeric character.

    Examples:
        "Starting ip"  → "startingip"
        "Network"      → "network"
        "networkIp"    → "networkip"
    """
    return "".join(c for c in s.lower() if c.isalnum())


def _eid_for_intent_field_key_bridge(
    all_elements: list[dict[str, Any]], role: str, name: str
) -> str | None:
    """Conservative bridge from intent name to element via field_key.

    Only called from ``_eid_for_intent`` after exact-match returns no hits.
    Same tie-break order as the primary path (required → enabled → first).

    Match condition (``ni`` = normalized intent, ``fk`` = normalized field_key):
      - ``len(ni) >= 4``                          (blocks "ip", "id", etc.)
      - ``ni == fk`` OR ``fk.startswith(ni)`` OR ``ni.startswith(fk)``
    """
    from backend.core.logging import get_logger  # noqa: PLC0415

    _log = get_logger(__name__)

    ni = _normalize_for_field_key(name)
    if len(ni) < 4:
        return None

    bridge_candidates = []
    for el in all_elements:
        if el.get("role") != role:
            continue
        fk_raw = el.get("field_key")
        if not isinstance(fk_raw, str) or not fk_raw:
            continue
        if not isinstance(el.get("eid"), str):
            continue
        fk = _normalize_for_field_key(fk_raw)
        if ni == fk or fk.startswith(ni) or ni.startswith(fk):
            bridge_candidates.append(el)

    if not bridge_candidates:
        return None

    # Tie-break — required first, then enabled, then first.
    pool = bridge_candidates
    required = [c for c in pool if c.get("required") is True]
    if required:
        pool = required
    else:
        enabled = [c for c in pool if c.get("enabled") is True]
        if enabled:
            pool = enabled

    winner = pool[0]
    matched_fk = winner["field_key"]
    eid = winner["eid"]
    _log.info(
        "eid_for_intent_field_key_bridge",
        intent_name=name,
        field_key=matched_fk,
        eid=eid,
    )
    return eid


def _reverse_lookup_eid(target_loc: Any, locator_map: dict[str, Any]) -> str | None:
    """Find which eid in ``locator_map`` points at the same DOM element as ``target_loc``.

    Compares bounding boxes (rounded to int) — two interactive elements
    on the Cisco WebUI never share the same bbox in practice. Falls back
    to None if either bbox is unavailable.
    """
    try:
        target_bbox = target_loc.bounding_box(timeout=_ACT_TIMEOUT_MS)
    except Exception:
        return None
    if target_bbox is None:
        return None

    tx, ty = int(round(target_bbox["x"])), int(round(target_bbox["y"]))
    tw, th = int(round(target_bbox["width"])), int(round(target_bbox["height"]))

    for eid, cand_loc in locator_map.items():
        try:
            cand_bbox = cand_loc.bounding_box(timeout=_ACT_TIMEOUT_MS)
        except Exception:
            continue
        if cand_bbox is None:
            continue
        if (
            int(round(cand_bbox["x"])) == tx
            and int(round(cand_bbox["y"])) == ty
            and int(round(cand_bbox["width"])) == tw
            and int(round(cand_bbox["height"])) == th
        ):
            return eid
    return None


def _do_act_by_intent(
    page: Any,
    locator_map: dict[str, Any],
    current_view_id: str | None,
    msg: dict[str, Any],
    ev: Any,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Resolve a planner intent (role + name) to an eid, then dispatch into ``_do_act``.

    Reuses `login.first_match` — the canonical strategy walker — rather
    than introducing a fourth variant. Re-describes child-side so the
    returned `chosen_eid` always aligns with a fresh view.

    Intent shape: ``{"role": str, "name": str, "action": str,
    "value": str | None}``. Action validation falls through to `_do_act`.
    """
    import hashlib  # noqa: PLC0415

    from backend.core.settings import get_settings  # noqa: PLC0415
    from backend.webui_agent.login import first_match  # noqa: PLC0415
    from backend.webui_agent.semantic_dom import describe_page  # noqa: PLC0415
    from backend.webui_agent.vision_fallback import (  # noqa: PLC0415
        evict_from_selector_cache,
        resolve_via_vision,
    )

    # Reactive resolution chain (runs in order; first hit wins):
    #   1. EID forward-lookup: scan the current describe view for role+name — cheap,
    #      uses the same spatial-label naming the inner LLM saw, no Anthropic call.
    #   2. Vision fallback (resolve_via_vision): cache hit = ~free; miss = Anthropic call
    #      + result cached. Handles elements describe_page cannot surface under the
    #      planner's role+name (e.g. Network textbox with spatial-only label).
    #   3. Heuristic first_match: role/name → label → text strategies via Playwright.
    #   4. unknown_eid: all three paths returned None.
    # _SENSITIVE_DENY_LIST is enforced on ALL branches — vision path checks inside
    # _try_act_with_vision; eid/first_match path checks before the final dispatch.

    intent = msg.get("intent") or {}
    role = intent.get("role")
    name = intent.get("name")
    action = intent.get("action")
    value = intent.get("value")

    if not isinstance(role, str) or not isinstance(name, str) or not isinstance(action, str):
        view, new_map = describe_page(page)
        return (
            {
                "ok": False,
                "failure_reason": "bad_intent",
                "chosen_eid": None,
                "view": view,
                "attempts": 0,
            },
            new_map,
            view["view_id"],
        )

    # Describe page once upfront — used by all resolution paths.
    view, fresh_map = describe_page(page)
    fresh_view_id: str = view["view_id"]

    settings = get_settings()
    page_url = page.url

    # -----------------------------------------------------------------------
    # Step 1: EID FORWARD-LOOKUP (cheap, deterministic).
    # Uses the same naming source the inner LLM saw (Phase 3.4 spatial labels
    # for inputs without ARIA labels), so role+name collisions resolve
    # correctly. Works for all elements already surfaced by describe_page
    # (e.g. the DHCP "Add" button). Returns None for elements describe_page
    # cannot surface (e.g. the DHCP "Network" textbox with spatial labels).
    # -----------------------------------------------------------------------
    chosen_loc: Any | None = None
    eid: str | None = _eid_for_intent(view, role, name)
    if eid is not None:
        chosen_loc = fresh_map.get(eid)
        # Belt-and-braces: if the eid is in the view but not the
        # locator_map (shouldn't happen, but bbox edge cases exist),
        # clear and fall through to vision / first_match.
        if chosen_loc is None:
            eid = None

    # -----------------------------------------------------------------------
    # Step 2: VISION FALLBACK (fires when eid lookup returned None).
    # resolve_via_vision is internally cache-aware — cache hit is ~free
    # (~0ms, no Anthropic call); miss calls Anthropic and caches the result
    # for future turns. Handles elements that describe_page cannot surface
    # under the planner's role+name (e.g. Network field with spatial labels).
    # -----------------------------------------------------------------------
    def _try_act_with_vision(
        selector: str, attempt: int
    ) -> tuple[dict[str, Any], dict[str, Any], str | None] | None:
        """Build synthetic_eid + synthetic_msg and call _do_act once.

        Returns (reply, new_map, new_vid) on success or staleness signal,
        None on hard exception (let caller fall through to heuristics).
        """
        try:
            vision_loc = page.locator(selector)
            synthetic_eid = f"vision_{hashlib.sha1(selector.encode()).hexdigest()[:8]}"
            fresh_map_with_vision = {**fresh_map, synthetic_eid: vision_loc}

            # Security guard: vision path must enforce the same
            # _SENSITIVE_DENY_LIST as the heuristic path. Without this check,
            # a prompt-injected intent like {role: button, name: "Reboot"}
            # would resolve via vision and click Reboot, bypassing the
            # deny-list entirely. Mirror the heuristic probe against the
            # locator's accessible name.
            try:
                accessible_name = (vision_loc.get_attribute("aria-label") or "").strip()
                if not accessible_name:
                    accessible_name = (vision_loc.text_content() or "").strip()
            except Exception:  # noqa: BLE001
                accessible_name = ""
            name_lower = accessible_name.lower()
            matched_phrase = next((p for p in _SENSITIVE_DENY_LIST if p in name_lower), None)
            if matched_phrase is not None:
                return (
                    {
                        "ok": False,
                        "failure_reason": "sensitive_text_denied",
                        "denied_phrase": matched_phrase,
                        "accessible_name": accessible_name,
                        "chosen_eid": None,
                        "view": view,
                        "attempts": 0,
                        "resolved_via": "vision_denied",
                        "vision_attempt": attempt,
                    },
                    fresh_map_with_vision,
                    fresh_view_id,
                )

            # Cancel-resolution guard (plan #2): an apply/save/submit/ok intent
            # must never be satisfied by a Cancel/Close control even when vision
            # picks it — clicking Cancel discards the form. Refuse → unknown_eid.
            if _is_apply_intent(name) and _is_cancel_control_name(accessible_name):
                from backend.core.logging import get_logger  # noqa: PLC0415

                get_logger(__name__).info(
                    "apply_intent_cancel_resolution_denied",
                    intent_name=name,
                    accessible_name=accessible_name,
                    resolved_via="vision",
                )
                return (
                    {
                        "ok": False,
                        "failure_reason": "unknown_eid",
                        "denied_reason": "apply_intent_resolved_to_cancel",
                        "accessible_name": accessible_name,
                        "chosen_eid": None,
                        "view": view,
                        "attempts": 0,
                        "resolved_via": "vision_denied",
                        "vision_attempt": attempt,
                    },
                    fresh_map_with_vision,
                    fresh_view_id,
                )

            synthetic_msg = {
                "view_id": fresh_view_id,
                "eid": synthetic_eid,
                "action": action,
                "value": value,
            }
            reply, new_map, new_vid = _do_act(
                page, fresh_map_with_vision, fresh_view_id, synthetic_msg, ev
            )
            reply["chosen_eid"] = synthetic_eid
            reply["resolved_via"] = "vision"
            reply["vision_attempt"] = attempt
            return reply, new_map, new_vid
        except Exception as exc:  # noqa: BLE001
            from backend.core.logging import get_logger  # noqa: PLC0415

            get_logger(__name__).warning(
                "vision_act_exception",
                error=str(exc),
                error_type=type(exc).__name__,
                attempt=attempt,
            )
            return None

    if chosen_loc is None:
        # eid lookup returned None — try vision fallback.
        vision_selector = resolve_via_vision(page, intent, ev, settings)
        if vision_selector is not None:
            result = _try_act_with_vision(vision_selector, attempt=1)
            if result is not None:
                reply, new_map, new_vid = result
                # Eviction + retry on failure signals indicating a stale or
                # bad cached selector. unknown_error added in chunk 14h-E after
                # live smoke act_20260523_48a212: a poisoned cache entry
                # (button:has-text('Add') from a prior tightening-prompt-pre-fix
                # session) kept failing with unknown_error, but the narrower
                # STALENESS set never evicted it — cache stayed poisoned
                # forever. Including unknown_error is over-eviction by design;
                # transient failures cost one extra vision call to repopulate.
                STALENESS = {
                    "element_hidden",
                    "element_disabled",
                    "element_intercepted",
                    "unknown_error",
                }
                if reply.get("ok") is False and reply.get("failure_reason") in STALENESS:
                    evicted = evict_from_selector_cache(
                        settings.selector_cache_path, role, name, page_url
                    )
                    if evicted:
                        # Retry vision: cache was evicted, so resolve_via_vision
                        # will go to Anthropic for a fresh selector.
                        retry_selector = resolve_via_vision(page, intent, ev, settings)
                        if retry_selector is not None and retry_selector != vision_selector:
                            retry_result = _try_act_with_vision(retry_selector, attempt=2)
                            if retry_result is not None:
                                reply, new_map, new_vid = retry_result
                return reply, new_map, new_vid

    # -----------------------------------------------------------------------
    # Step 3: HEURISTIC first_match (last resort — both eid lookup and
    # vision returned None).
    # -----------------------------------------------------------------------
    if chosen_loc is None:
        strategies: list[dict[str, Any]] = [
            {"role": role, "name": name},
            {"label": name},
            {"text": name},
        ]
        chosen_loc = first_match(page, strategies)
        if chosen_loc is None:
            return (
                {
                    "ok": False,
                    "failure_reason": "unknown_eid",
                    "chosen_eid": None,
                    "view": view,
                    "attempts": 0,
                },
                fresh_map,
                fresh_view_id,
            )

    # -----------------------------------------------------------------------
    # Sensitive-text deny-list: refuse to act on locators whose accessible
    # name matches dangerous operations. Defends against prompt-injected
    # intents that would resolve to "Factory Reset" / "Reboot" / etc.
    # Applies to ALL resolution paths: eid-resolved AND first_match-resolved.
    # (Vision-resolved locators are checked inside _try_act_with_vision above.)
    # -----------------------------------------------------------------------
    try:
        accessible_name = (chosen_loc.get_attribute("aria-label") or "").strip()
        if not accessible_name:
            accessible_name = (chosen_loc.text_content() or "").strip()
    except Exception:  # noqa: BLE001
        accessible_name = ""
    name_lower = accessible_name.lower()
    matched_phrase = next((p for p in _SENSITIVE_DENY_LIST if p in name_lower), None)
    if matched_phrase is not None:
        return (
            {
                "ok": False,
                "failure_reason": "sensitive_text_denied",
                "denied_phrase": matched_phrase,
                "accessible_name": accessible_name,
                "chosen_eid": None,
                "view": view,
                "attempts": 0,
            },
            fresh_map,
            fresh_view_id,
        )

    # Cancel-resolution guard (plan #2): an apply/save/submit/ok intent must
    # NEVER be satisfied by a Cancel/Close control. Resolving "Apply to Device"
    # onto "Cancel" silently discards the user's form input and drives the
    # fill→cancel→refill loop. Refuse it — return unknown_eid rather than click
    # Cancel. Applies to the eid-forward and first_match paths (the vision path
    # enforces the same guard inside _try_act_with_vision).
    if _is_apply_intent(name) and _is_cancel_control_name(accessible_name):
        from backend.core.logging import get_logger  # noqa: PLC0415

        get_logger(__name__).info(
            "apply_intent_cancel_resolution_denied",
            intent_name=name,
            accessible_name=accessible_name,
        )
        return (
            {
                "ok": False,
                "failure_reason": "unknown_eid",
                "denied_reason": "apply_intent_resolved_to_cancel",
                "accessible_name": accessible_name,
                "chosen_eid": None,
                "view": view,
                "attempts": 0,
            },
            fresh_map,
            fresh_view_id,
        )

    # Reverse-lookup the chosen locator's eid (only needed for the
    # first_match fallback path — the forward path already set eid).
    if eid is None:
        eid = _reverse_lookup_eid(chosen_loc, fresh_map)
    if eid is None:
        return (
            {
                "ok": False,
                "failure_reason": "unknown_eid",
                "chosen_eid": None,
                "view": view,
                "attempts": 0,
            },
            fresh_map,
            fresh_view_id,
        )

    # Dispatch into the same _do_act machinery (self-heal, never-retry-click).
    synthetic_msg = {
        "view_id": fresh_view_id,
        "eid": eid,
        "action": action,
        "value": value,
    }
    reply, new_map, new_vid = _do_act(page, fresh_map, fresh_view_id, synthetic_msg, ev)
    reply["chosen_eid"] = eid
    return reply, new_map, new_vid


# ---------------------------------------------------------------------------
# C3: Atlas act-path helpers (ADDITIVE — do NOT modify existing ops/functions)
# ---------------------------------------------------------------------------


def _settle_explicit(page: Any, expect_locator: Any = None, timeout_ms: int = 2000) -> None:
    """Settle the page after an atlas-driven act, WITHOUT networkidle.

    Two strategies (best-effort — all exceptions are swallowed):
    1. If ``expect_locator`` is given, wait for it to be visible within
       ``timeout_ms``.  Useful after an apply click that triggers a banner.
    2. Otherwise, wait for ``"domcontentloaded"`` with a small bounded
       fallback sleep.  This REPLACES the expensive networkidle used by
       ``_settle_page`` — the atlas path doesn't need to wait for every
       Angular XHR to drain.
    """
    from playwright.sync_api import expect  # noqa: PLC0415

    if expect_locator is not None:
        with contextlib.suppress(Exception):
            expect(expect_locator).to_be_visible(timeout=timeout_ms)
        return

    import time as _time  # noqa: PLC0415

    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            _time.sleep(0.15)


def _do_act_by_field(
    page: Any,
    atlas: Any,
    msg: dict[str, Any],
    ev: Any,
) -> dict[str, Any]:
    """Atlas-driven act: locate + apply + read-back-verify a single field.

    Self-heal taxonomy (failure_reason values):
    - ``"sensitive_denied"``        : field_key / label / value matched deny-list.
    - ``"unknown_field_key"``       : field_key not found in the atlas.
    - ``"verify_mismatch"``         : adapter acted but read-back disagrees.
    - ``"element_intercepted"``     : PlaywrightTimeoutError after 2 attempts.
    - ``"value_rejected"``          : ValueError from adapter (dead-end, no retry).
    - ``"unmapped_field"``          : LocatorResolutionError — route to vision rung.
    - ``"unknown_error"``           : any other exception.
    """
    import sys as _sys  # noqa: PLC0415

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

    from backend.webui_agent.atlas.adapters import LocatorResolutionError  # noqa: PLC0415

    # Resolve via sys.modules so tests can patch
    # ``backend.webui_agent._playwright_subprocess.get_adapter``.
    _this_mod = _sys.modules[__name__]
    _get_adapter = _this_mod.get_adapter

    field_key = str(msg.get("field_key", ""))
    value = msg.get("value")

    # ---- deny-list guard ----
    deny_target = (
        field_key.lower()
        + " "
        + str(value).lower()
        + " "
        + (atlas.field_by_key(field_key).label.lower() if atlas and atlas.field_by_key(field_key) else "")
    )
    for phrase in _SENSITIVE_DENY_LIST:
        if phrase in deny_target:
            return {"ok": False, "failure_reason": "sensitive_denied"}

    # ---- atlas lookup ----
    field = atlas.field_by_key(field_key) if atlas is not None else None
    if field is None:
        return {
            "ok": False,
            "failure_reason": "unknown_field_key",
            "field_key": field_key,
        }

    adapter = _get_adapter(field.widget)

    max_attempts = 2
    for attempt_idx in range(max_attempts):
        try:
            adapter.apply(page, field, value)

            # Read-back self-verify.
            rb = adapter.read_back(page, field)
            if rb is not None:
                # Bool widgets: compare bool.
                if isinstance(rb, bool):
                    intended_bool = value in (True, "true", "1", "yes", "on")
                    if rb != intended_bool:
                        return {
                            "ok": False,
                            "failure_reason": "verify_mismatch",
                            "field_key": field_key,
                            "expected": value,
                            "got": rb,
                        }
                else:
                    # String compare, case-insensitive + trimmed.
                    mismatch = str(rb).strip().lower() != str(value).strip().lower()
                    # Kendo combobox read-back is the backing <select> VALUE
                    # attribute, which can differ from the chosen display text
                    # (e.g. value="24" vs text="255.255.255.0"). The atlas only
                    # stores option texts, so a value/text difference can't be
                    # disambiguated here — treat combobox read-back as ADVISORY
                    # (the CLI running-config verify is the real backstop) so a
                    # correct selection is never reported as verify_mismatch.
                    # Strict compare stays for text inputs — that is what catches
                    # the fill-corruption case (e.g. the Starting-ip concat bug).
                    if mismatch and field.widget == "kendo_combobox":
                        mismatch = False
                    if mismatch:
                        return {
                            "ok": False,
                            "failure_reason": "verify_mismatch",
                            "field_key": field_key,
                            "expected": value,
                            "got": rb,
                        }

            _settle_explicit(page)
            ev.step(f"actfield-{field_key}", page)
            return {"ok": True, "field_key": field_key, "attempts": attempt_idx}

        except PlaywrightTimeoutError:
            if attempt_idx + 1 < max_attempts:
                continue
            return {
                "ok": False,
                "failure_reason": "element_intercepted",
                "field_key": field_key,
                "attempts": attempt_idx,
            }

        except ValueError as exc:
            return {
                "ok": False,
                "failure_reason": "value_rejected",
                "field_key": field_key,
                "error": str(exc),
            }

        except LocatorResolutionError:
            return {"ok": False, "failure_reason": "unmapped_field", "field_key": field_key}

        except Exception as exc:  # noqa: BLE001
            with contextlib.suppress(Exception):
                ev.dump_dom(page, f"99-actfield-error-{field_key}")
            # Visibility-first (live-smoke-iteration): surface the real exception
            # in the log, not just "unknown_error" — strict-mode violations and
            # other Playwright errors are otherwise only in the DOM dump.
            with contextlib.suppress(Exception):
                from backend.core.logging import get_logger  # noqa: PLC0415

                get_logger(__name__).warning(
                    "act_field_unknown_error",
                    field_key=field_key,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
            return {
                "ok": False,
                "failure_reason": "unknown_error",
                "field_key": field_key,
                "error": str(exc),
                "exc_type": type(exc).__name__,
            }

    # Unreachable — every loop branch returns.
    return {  # pragma: no cover
        "ok": False,
        "failure_reason": "element_intercepted",
        "field_key": field_key,
        "attempts": max_attempts - 1,
    }


def _locate_control(page: Any, control: Any) -> Any:
    """Resolve a ControlSpec to a live Playwright Locator.

    Tries the primary locator then each fallback, returning the first with
    count > 0.  Raises ``LocatorResolutionError`` when nothing resolves.
    """
    from backend.webui_agent.atlas.adapters import (  # noqa: PLC0415
        LocatorResolutionError,
        resolve_locator,
    )

    if control.locator is None:
        raise LocatorResolutionError(control.key)

    candidates = [control.locator, *control.locator.fallbacks]
    for locspec in candidates:
        try:
            loc = resolve_locator(page, locspec)
            if loc.count() > 0:
                return loc
        except Exception:  # noqa: BLE001
            continue

    raise LocatorResolutionError(control.key)


def _do_apply_control(
    page: Any,
    atlas: Any,
    msg: dict[str, Any],
    ev: Any,
) -> dict[str, Any]:
    """Click an atlas apply control (the router-write submit button).

    CLAUDE.md §4: NEVER retried on TimeoutError — the XHR may have
    already landed at the router before Playwright sees the timeout.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

    from backend.webui_agent.atlas.adapters import (  # noqa: PLC0415
        CLICK_TIMEOUT_MS,
        LocatorResolutionError,
    )

    if atlas is None or not atlas.apply_controls:
        return {"ok": False, "failure_reason": "no_apply_control"}

    key = msg.get("key")

    # Pick the target control: by key, then first is_router_write, then first.
    control = None
    if key is not None:
        for c in atlas.apply_controls:
            if c.key == key:
                control = c
                break
    if control is None:
        for c in atlas.apply_controls:
            if c.is_router_write:
                control = c
                break
    if control is None:
        control = atlas.apply_controls[0]

    # Apply→Cancel guard.
    if _is_cancel_control_name(control.label):
        return {"ok": False, "failure_reason": "apply_resolved_to_cancel"}

    try:
        loc = _locate_control(page, control)
    except LocatorResolutionError:
        return {"ok": False, "failure_reason": "unmapped_field"}

    try:
        loc.click(timeout=CLICK_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        # NEVER retry — CLAUDE.md §4.
        return {"ok": False, "failure_reason": "click_timeout_unsafe_retry"}
    except LocatorResolutionError:
        return {"ok": False, "failure_reason": "unmapped_field"}
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            ev.dump_dom(page, "99-apply-control-error")
        return {
            "ok": False,
            "failure_reason": "unknown_error",
            "error": str(exc),
            "exc_type": type(exc).__name__,
        }

    _settle_explicit(page)
    ev.step("apply-device", page)
    return {"ok": True}


def _run_session_loop(init_payload: dict[str, Any]) -> None:
    """Phase 4 long-lived session: log in once, handle ops until shutdown.

    Recognised ops:
      - `open(path)`           — navigate + describe; returns view
      - `describe`             — fresh describe of the current page
      - `verify(text)`         — text-presence check, read-only
      - `act` / `act_by_intent` — placeholder in slice 1, returns NotImplemented
      - `shutdown`             — clean exit
      C3 additions (ADDITIVE):
      - `perceive`             — atlas-driven page observation
      - `act_field`            — atlas-driven field act + read-back verify
      - `apply_control`        — atlas apply-control click (router write)
      - `verify_a11y`          — accessibility-tree text scan

    All replies are `{"ok": bool, ...}` JSON lines. On the FIRST line
    after a successful login the child emits `{"ok": true, "ready": true,
    "evidence_dir": "..."}` — the parent must read this before issuing
    any op (handshake).
    """
    # Lazy imports — keep cold start cheap and the one-shot path unaffected.
    from backend.webui_agent.browser import webui_browser
    from backend.webui_agent.evidence import EvidenceCollector
    from backend.webui_agent.login import login

    action_id = str(init_payload.get("action_id") or "session")
    headless = init_payload.get("headless")

    ev = EvidenceCollector("generic_session", action_id=action_id)
    # locator_map is rebuilt on every describe; held child-side only.
    # current_view_id tracks the latest view's view_id so `act` can reject
    # stale eid references from the planner (eids renumber per describe).
    locator_map: dict[str, Any] = {}
    current_view_id: str | None = None
    # C3: atlas state — lazily built on first "perceive" op.
    atlas_store: Any = None
    current_atlas: Any = None

    try:
        with webui_browser(headless=headless) as page:
            if not login(page):
                ev.dump_dom(page, "99-login-failed")
                _reply(
                    {
                        "ok": False,
                        "error": "WebUI login failed",
                        "exc_type": "RuntimeError",
                    }
                )
                return
            ev.step("00-logged-in", page)

            # Handshake — parent blocks on this readline before sending ops.
            _reply(
                {
                    "ok": True,
                    "ready": True,
                    "evidence_dir": str(ev.session_dir),
                }
            )

            while True:
                line = sys.stdin.readline()
                if not line:  # EOF — parent closed stdin
                    return

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    _reply(
                        {
                            "ok": False,
                            "error": f"invalid JSON message: {exc!s}",
                            "exc_type": "JSONDecodeError",
                        }
                    )
                    continue

                op = msg.get("op")

                if op == "shutdown":
                    _reply({"ok": True, "shutdown": True})
                    return

                try:
                    # Re-login if the Cisco WebUI session timed out
                    # between ops. Cheap if we're still logged in
                    # (string check on page.url).
                    _relogin_if_needed(page)

                    if op == "open":
                        from backend.core.settings import get_settings  # noqa: PLC0415,I001 — lazy import

                        raw_path = str(msg["path"])
                        target = _resolve_target_url(page, raw_path)
                        # wait_until="domcontentloaded" so we don't time out on
                        # third-party network calls; Angular renders after.
                        page.goto(
                            target,
                            wait_until="domcontentloaded",
                            timeout=get_settings().webui_goto_timeout_ms,
                        )
                        _settle_page(page)  # networkidle + fallback before first describe
                        # Label uses the path tail so screenshots stay scannable.
                        label_tail = raw_path.split("/")[-1] or "root"
                        ev.step(f"goto-{label_tail}", page)
                        view, locator_map = _describe_with_retry(page, max_attempts=2)
                        current_view_id = view["view_id"]
                        _reply({"ok": True, "view": view})

                    elif op == "describe":
                        view, locator_map = _describe_with_retry(page, max_attempts=2)
                        current_view_id = view["view_id"]
                        _reply({"ok": True, "view": view})

                    elif op == "verify":
                        text = str(msg.get("text", ""))
                        # Use page.content() — full DOM HTML — for substring check.
                        # The planner can pass a verbose phrase ("VLAN 46 created")
                        # to look for the success banner.
                        present = text in page.content()
                        _reply(
                            {
                                "ok": True,
                                "present": present,
                                "url": page.url,
                            }
                        )

                    elif op == "act":
                        reply, locator_map, current_view_id = _do_act(
                            page, locator_map, current_view_id, msg, ev
                        )
                        _reply(reply)

                    elif op == "act_by_intent":
                        reply, locator_map, current_view_id = _do_act_by_intent(
                            page, locator_map, current_view_id, msg, ev
                        )
                        _reply(reply)

                    # ----------------------------------------------------------
                    # C3: Atlas act-path ops (ADDITIVE)
                    # ----------------------------------------------------------

                    elif op == "perceive":
                        from backend.core.settings import get_settings  # noqa: PLC0415
                        from backend.webui_agent.atlas.store import AtlasStore  # noqa: PLC0415
                        from backend.webui_agent.perceive import perceive_page  # noqa: PLC0415

                        fp = str(msg.get("device_fingerprint") or "unknown__unknown")
                        if atlas_store is None or atlas_store.fingerprint != fp:
                            atlas_store = AtlasStore(get_settings().atlas_dir, fp)
                        result = perceive_page(
                            page,
                            atlas_store,
                            device_fingerprint=fp,
                            route=msg.get("route"),
                        )
                        current_atlas = result.atlas
                        _reply(
                            {
                                "ok": True,
                                "view": result.view,
                                "drift": result.drift,
                                "captured": result.captured,
                                "missing_required": result.missing_required,
                                "unmapped_fields": result.unmapped_fields,
                            }
                        )

                    elif op == "act_field":
                        _reply(_do_act_by_field(page, current_atlas, msg, ev))

                    elif op == "apply_control":
                        _reply(_do_apply_control(page, current_atlas, msg, ev))

                    elif op == "verify_a11y":
                        from backend.webui_agent.atlas.reconcile import (  # noqa: PLC0415,I001
                            flatten_interactive,
                        )

                        contains = str(msg.get("contains", ""))
                        snap = page.accessibility.snapshot(interesting_only=True)
                        nodes = flatten_interactive(snap)
                        present = any(
                            contains.lower()
                            in (
                                str(n.get("name", "")) + " " + str(n.get("value", ""))
                            ).lower()
                            for n in nodes
                        )
                        _reply({"ok": True, "present": present})

                    else:
                        _reply(
                            {
                                "ok": False,
                                "error": f"unknown op {op!r}",
                                "exc_type": "ValueError",
                            }
                        )

                except Exception as exc:  # noqa: BLE001
                    # Dump DOM for forensics, then surface to parent.
                    with contextlib.suppress(Exception):
                        ev.dump_dom(page, f"99-error-{op or 'unknown'}")
                    _reply(
                        {
                            "ok": False,
                            "error": str(exc) or repr(exc),
                            "exc_type": type(exc).__name__,
                        }
                    )

    except Exception as exc:  # noqa: BLE001
        # Top-level failure: browser launch crashed, login imploded, etc.
        traceback.print_exc(file=sys.stderr)
        _reply(
            {
                "ok": False,
                "error": str(exc) or repr(exc),
                "exc_type": type(exc).__name__,
            }
        )


def main() -> None:
    # Configure structlog so log lines from inside the flow land in
    # the same logs/actions.log the parent writes to. Without this,
    # `log.info(...)` calls from page objects / login / etc. would go
    # nowhere (the subprocess has its own logging state).
    from backend.core.logging import configure_logging
    from backend.core.settings import get_settings

    settings = get_settings()
    configure_logging(log_level=settings.log_level, logs_dir=settings.logs_dir)

    try:
        # readline (not read) so session mode can keep reading more lines
        # after the initial config. For one-shot the parent passes a single
        # JSON object with no trailing newline; readline returns it at EOF.
        first_line = sys.stdin.readline()
        payload = json.loads(first_line)
    except (json.JSONDecodeError, TypeError) as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": f"invalid subprocess payload: {exc!s}",
                    "exc_type": type(exc).__name__,
                }
            )
        )
        sys.exit(1)

    if payload.get("mode") == "session":
        _run_session_loop(payload)
        return

    # ---- One-shot mode (existing path, unchanged semantics) ----
    try:
        flow_name = payload["flow"]
        args = payload.get("args", {})
    except (KeyError, TypeError) as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": f"invalid subprocess payload: missing 'flow' ({exc!s})",
                    "exc_type": type(exc).__name__,
                }
            )
        )
        sys.exit(1)

    handler = _DISPATCH.get(flow_name)
    if handler is None:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": f"unknown flow {flow_name!r}; known: {sorted(_DISPATCH)}",
                    "exc_type": "ValueError",
                }
            )
        )
        sys.exit(1)

    try:
        result = handler(args)
    except Exception as exc:  # noqa: BLE001
        # Full traceback to stderr so the parent can include it in
        # logs/actions.log if it wants to. Structured JSON to stdout
        # so the parent has a clean machine-readable error to report
        # in the API response.
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc) or repr(exc),
                    "exc_type": type(exc).__name__,
                }
            )
        )
        sys.exit(1)

    sys.stdout.write(json.dumps({"ok": True, "result": result}))


if __name__ == "__main__":
    main()
