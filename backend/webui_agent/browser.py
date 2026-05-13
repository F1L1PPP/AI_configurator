"""Playwright Chromium launcher tuned for the Cisco C1111 WebUI.

Why a wrapper at all instead of using Playwright directly in each flow:
- Cert handling needs both `ignore_https_errors=True` on the context AND
  `--ignore-certificate-errors` as a Chromium flag (the C1111 self-signed
  cert sometimes triggers the warning page despite the context flag alone).
- Viewport must be pinned for reproducible screenshot evidence.
- Console + pageerror events should land in structlog, not print, so they
  show up alongside CLI tool calls in logs/actions.log.
- networkidle is unreliable on Angular WebUIs (long-polling never stops);
  we suppress that timeout instead of letting it bubble.

Usage:
    from backend.webui_agent.browser import webui_browser

    with webui_browser() as page:
        page.goto("https://192.168.10.1")
        ...
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from backend.core.logging import get_logger

log = get_logger(__name__)

VIEWPORT = {"width": 1400, "height": 900}


def _env_truthy(name: str) -> bool:
    """Treat 1/true/yes (any case) as on. Empty / unset → off."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_headless(explicit: bool | None) -> bool:
    """Pick headless mode: explicit caller > PLAYWRIGHT_HEADLESS > CI=1 > False.

    Audit #26 — playground + production both need to flip to headless when
    running in CI (no display server). Env var override lets you toggle
    without code changes.
    """
    if explicit is not None:
        return explicit
    return _env_truthy("PLAYWRIGHT_HEADLESS") or _env_truthy("CI")


@contextmanager
def webui_browser(
    *,
    headless: bool | None = None,
    slow_mo: int | None = None,
) -> Iterator[Page]:
    """Launch Chromium configured for the Cisco WebUI and yield a fresh Page.

    Args:
        headless: True for CI/smoke runs. None (default) reads
                  PLAYWRIGHT_HEADLESS or CI env vars; falls back to False
                  (dev / watch-it-click).
        slow_mo:  ms between actions. None (default) reads PLAYWRIGHT_SLOW_MO
                  env var or falls back to 400 (headed) / 0 (headless).

    The browser closes automatically on context exit.
    """
    headless_final = _resolve_headless(headless)
    if slow_mo is None:
        env_slow = os.environ.get("PLAYWRIGHT_SLOW_MO", "").strip()
        slow_mo = int(env_slow) if env_slow.isdigit() else (0 if headless_final else 400)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless_final,
            slow_mo=slow_mo,
            args=["--ignore-certificate-errors"],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            viewport=VIEWPORT,
        )
        page = context.new_page()
        _attach_listeners(page)
        log.info(
            "webui_browser_launched",
            headless=headless_final,
            slow_mo=slow_mo,
            viewport=VIEWPORT,
        )
        try:
            yield page
        finally:
            browser.close()
            log.info("webui_browser_closed")


def wait_for_networkidle(page: Page, timeout_ms: int = 10_000) -> None:
    """Wait for networkidle, suppressing the timeout exception.

    Angular-heavy WebUIs (like IOS XE 17.x) keep long-poll connections open
    indefinitely, so networkidle never fires. Suppress the timeout so flows
    can continue. Callers that strictly need the page to be settled should
    use `page.wait_for_selector(...)` against a known post-load element.
    """
    with contextlib.suppress(PWTimeout):
        page.wait_for_load_state("networkidle", timeout=timeout_ms)


def _attach_listeners(page: Page) -> None:
    """Forward browser console + pageerror events into structlog."""
    page.on(
        "console",
        lambda msg: log.debug("browser_console", level=msg.type, text=msg.text[:300]),
    )
    page.on(
        "pageerror",
        lambda exc: log.warning("browser_pageerror", error=str(exc)),
    )
