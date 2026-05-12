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
from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from backend.core.logging import get_logger

log = get_logger(__name__)

VIEWPORT = {"width": 1400, "height": 900}


@contextmanager
def webui_browser(*, headless: bool = False, slow_mo: int = 400) -> Iterator[Page]:
    """Launch Chromium configured for the Cisco WebUI and yield a fresh Page.

    Args:
        headless: False for dev (you watch it click), True for CI/smoke runs.
        slow_mo:  ms between actions; only meaningful headed.

    The browser closes automatically on context exit.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            args=["--ignore-certificate-errors"],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            viewport=VIEWPORT,
        )
        page = context.new_page()
        _attach_listeners(page)
        log.info("webui_browser_launched", headless=headless, viewport=VIEWPORT)
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
