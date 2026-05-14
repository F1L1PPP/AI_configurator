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
    from backend.webui_agent.semantic_dom import describe_page

    action_id = str(init_payload.get("action_id") or "session")
    headless = init_payload.get("headless")

    ev = EvidenceCollector("generic_session", action_id=action_id)
    # locator_map is rebuilt on every describe; held child-side only.
    # Phase 4 slice 2 will also track `current_view_id` to reject stale eids.
    locator_map: dict[str, Any] = {}

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
                        path = str(msg["path"])
                        page.goto(path)
                        # Label uses the path tail so screenshots stay scannable.
                        label_tail = path.split("/")[-1] or "root"
                        ev.step(f"goto-{label_tail}", page)
                        view, locator_map = describe_page(page)
                        _reply({"ok": True, "view": view})

                    elif op == "describe":
                        view, locator_map = describe_page(page)
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

                    elif op in ("act", "act_by_intent"):
                        _reply(
                            {
                                "ok": False,
                                "error": f"op {op!r} arrives in Phase 4 slice 2",
                                "exc_type": "NotImplementedError",
                            }
                        )

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
