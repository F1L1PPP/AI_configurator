"""WebUI login + session-keepalive for the Cisco C1111.

The Cisco IOS XE 17.x WebUI is Angular with class-based selectors that
change between builds, so login walks a fallback chain stored in
`selectors/iosxe_default.yaml`. The session times out after ~5 minutes of
idle, so we either:
- relogin lazily before each navigation (`ensure_logged_in`), or
- run a background keepalive thread that nudges the page every 4 minutes.

Credentials default to `.env` values (router_webui_user, router_webui_password,
router_webui_base_url) — you can pass overrides for testing.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any

from backend.core.logging import get_logger
from backend.core.settings import get_settings
from backend.webui_agent.browser import wait_for_networkidle
from backend.webui_agent.selectors import load_selectors

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

log = get_logger(__name__)

KEEPALIVE_INTERVAL_S = 240  # 4 minutes; WebUI idle timeout is 5 minutes


# ---------------------------------------------------------------------------
# Strategy resolver — walk the YAML fallback chain
# ---------------------------------------------------------------------------


def first_match(page: Page, strategies: list[dict[str, Any]]) -> Locator | None:
    """Return a locator for the first VISIBLE element that any strategy matches.

    Strategy keys understood: role (+ name), label, text, css.
    Order in the list is significance order — most stable first.

    Visibility matters: the Cisco IOS XE WebUI renders duplicate elements
    (the same nav text appears in a collapsed hidden menu AND in the
    visible sidebar). `.first` alone could pick the hidden mirror, which
    then causes `.click()` / `.fill()` to time out waiting for it to
    become actionable. Filtering to visible up front sidesteps that.

    If a strategy matches but no match is visible, we move to the next
    strategy (don't fall back to a hidden element).
    """
    for strat in strategies:
        try:
            loc = _build(page, strat)
        except Exception as exc:
            log.debug("strategy_build_failed", strat=strat, error=str(exc))
            continue
        if loc is None:
            continue
        try:
            n = loc.count()
        except Exception as exc:
            log.debug("strategy_count_failed", strat=strat, error=str(exc))
            continue
        if n == 0:
            continue

        # Pick the first visible match. Walk all matches in case the first
        # is the hidden mirror element.
        for i in range(n):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                continue
        # None of the matches are visible — try next strategy
        log.debug("strategy_no_visible_match", strat=strat, total_matches=n)
    return None


def _build(page: Page, strat: dict[str, Any]) -> Locator | None:
    """Convert one strategy dict into a Playwright Locator (no .count() yet).

    Strategy keys:
      role (+name) — page.get_by_role(role, name=name)
      label        — page.get_by_label(label, exact=False)
      role_text    — (role, name) tuple; page.get_by_role(role, name=name, exact=False)
                     Used as a role-constrained text match BEFORE bare text= fallback
                     to avoid matching same-text LINKS when looking for a textbox.
      text         — page.locator("text=…") last-resort generic text match
      css          — page.locator(selector)
    """
    if "role" in strat:
        name = strat.get("name")
        return (
            page.get_by_role(strat["role"], name=name) if name else page.get_by_role(strat["role"])
        )
    if "label" in strat:
        return page.get_by_label(strat["label"], exact=False)
    if "role_text" in strat:
        role, name = strat["role_text"]
        return page.get_by_role(role, name=name, exact=False)
    if "text" in strat:
        return page.locator(f"text={strat['text']}")
    if "css" in strat:
        return page.locator(strat["css"])
    return None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def login(
    page: Page,
    *,
    base_url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    selectors_map: str = "iosxe_default",
) -> bool:
    """Navigate to base_url and submit WebUI credentials. Returns success.

    Defaults pull from .env via Pydantic Settings. Pass explicit values for
    tests or for talking to a non-default device.
    """
    s = get_settings()
    base_url = base_url or s.router_webui_base_url
    username = username or s.router_webui_user
    password = password or s.router_webui_password

    selectors = load_selectors(selectors_map)["login"]

    log.info("webui_login_attempt", base_url=base_url, user=username)
    page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)
    wait_for_networkidle(page, 15_000)

    user_loc = first_match(page, selectors["username"])
    if user_loc is None:
        log.error("webui_login_no_username_field")
        return False
    user_loc.fill(username)

    pass_loc = first_match(page, selectors["password"])
    if pass_loc is None:
        log.error("webui_login_no_password_field")
        return False
    pass_loc.fill(password)

    submit_loc = first_match(page, selectors["submit"])
    if submit_loc is not None:
        submit_loc.click()
    else:
        log.info("webui_login_no_submit_button_using_enter")
        page.keyboard.press("Enter")

    wait_for_networkidle(page, 30_000)
    log.info("webui_login_complete", url=page.url)
    return True


# ---------------------------------------------------------------------------
# Session-expired detection + relogin
# ---------------------------------------------------------------------------


def is_session_expired(page: Page, selectors_map: str = "iosxe_default") -> bool:
    """Return True if the current page looks like the login page or a
    session-expired notice (i.e. we got bounced)."""
    selectors = load_selectors(selectors_map).get("session_expired", [])
    return first_match(page, selectors) is not None


def ensure_logged_in(page: Page, *, base_url: str | None = None) -> bool:
    """Relogin if the session has expired. No-op if still authenticated."""
    if not is_session_expired(page):
        return True
    log.info("webui_session_expired_relogin")
    return login(page, base_url=base_url)


# ---------------------------------------------------------------------------
# Keepalive — background thread that nudges the page every 4 min
# ---------------------------------------------------------------------------


def start_keepalive(page: Page, interval_s: int = KEEPALIVE_INTERVAL_S) -> threading.Event:
    """Start a background thread that triggers tiny activity on the page
    every `interval_s` seconds to reset the Cisco WebUI idle timer.

    Returns a stop-event; call `.set()` to halt the keepalive.

    WARNING — THREAD SAFETY (audit #9):
        Playwright's sync API is NOT thread-safe: you cannot call methods
        on the same Page object from two threads simultaneously. This
        keepalive runs in a background thread that calls `page.evaluate`,
        which conflicts with any foreground thread that's currently
        clicking/filling the page.

        Use rules:
        - For active flows (HostnamePage.goto / set_hostname / apply):
          DO NOT start keepalive. The user-driven actions already reset
          the idle timer on every click.
        - For long-idle scenarios (multi-step forms that pause for human
          input between steps): consider keepalive, but only between
          steps — call stop_keepalive() before the next page interaction.
        - Better long-term: migrate to Playwright's async API and run
          the keepalive coroutine on the same event loop. Deferred until
          Day 11 polish.

        Today, no production flow uses this keepalive. The function is
        kept available for future flows where it makes sense.
    """
    stop = threading.Event()

    def _loop() -> None:
        while not stop.wait(interval_s):
            try:
                page.evaluate("() => document.body && document.body.click()")
                log.debug("webui_keepalive_tick")
            except Exception as exc:
                log.warning("webui_keepalive_failed", error=str(exc))
                return

    t = threading.Thread(target=_loop, daemon=True, name="webui-keepalive")
    t.start()
    log.info("webui_keepalive_started", interval_s=interval_s)
    return stop


def stop_keepalive(stop_event: threading.Event) -> None:
    """Signal the keepalive thread to exit at the next interval boundary."""
    with contextlib.suppress(Exception):
        stop_event.set()
    log.info("webui_keepalive_stopped")
