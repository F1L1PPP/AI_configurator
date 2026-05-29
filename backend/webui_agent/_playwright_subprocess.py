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
    """
    if action == "click":
        locator.click(timeout=_ACT_TIMEOUT_CLICK_MS)
    elif action == "fill":
        locator.fill(str(value or ""), timeout=_ACT_TIMEOUT_FORM_MS)
    elif action == "select":
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
    """
    if not isinstance(role, str) or not isinstance(name, str):
        return None
    candidates = [
        el
        for el in list(view.get("elements") or []) + list(view.get("modals") or [])
        if el.get("role") == role and el.get("name") == name and isinstance(el.get("eid"), str)
    ]
    if not candidates:
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


def _run_session_loop(init_payload: dict[str, Any]) -> None:
    """Phase 4 long-lived session: log in once, handle ops until shutdown.

    Recognised ops:
      - `open(path)`           — navigate + describe; returns view
      - `describe`             — fresh describe of the current page
      - `verify(text)`         — text-presence check, read-only
      - `act` / `act_by_intent` — placeholder in slice 1, returns NotImplemented
      - `shutdown`             — clean exit

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
