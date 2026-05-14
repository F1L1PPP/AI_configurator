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
This module is that process. It reads a small JSON payload on stdin,
runs ONLY the Playwright steps for a single flow (no `_guard`, no
state mutation, no Netmiko — those stay in the parent), and writes
the result back on stdout.

Invocation (from the parent):
    proc = subprocess.run(
        [sys.executable, "-m", "backend.webui_agent._playwright_subprocess"],
        input=json.dumps({"flow": "add_access_vlan", "args": {...}}),
        capture_output=True, text=True, timeout=...
    )

Protocol:
  - stdin:  `{"flow": "<name>", "args": {<kwargs>}}`
  - stdout: `{"ok": true,  "result": {...}}` on success
            `{"ok": false, "error": "<msg>", "exc_type": "<name>"}` on failure
            (also exit code 1 on failure, traceback on stderr).

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
        payload_text = sys.stdin.read()
        payload = json.loads(payload_text)
        flow_name = payload["flow"]
        args = payload.get("args", {})
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        # Bad payload from parent — should never happen in normal use.
        # Surface a clear error so the parent's RuntimeError message is
        # actually informative.
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
