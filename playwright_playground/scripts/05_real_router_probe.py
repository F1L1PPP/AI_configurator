"""Script 5 — REAL ROUTER probe (Day 3 cert/login probe pulled forward to Day 2).

This is the first time we point Playwright at the actual Cisco C1111 WebUI
(scripts 01-04 ran against the local fake site). What this script proves:

  - Playwright launches headed Chromium against an HTTPS endpoint with a
    self-signed cert and `ignore_https_errors=True` lets us through cleanly.
  - The login form on the real Cisco IOS XE 17.6 WebUI accepts the credentials
    we set up in the cabled session (`cisco`/`cisco`).
  - After login, the WebUI now shows Configuration menus (NOT only the
    Monitoring/Dashboard restriction from §10's risk register).
  - The DOM is screenshotable end-to-end — every step's PNG lands in
    `playwright_playground/artifacts/05_real_router_<timestamp>/`.

Credentials come from `.env` via python-dotenv. NEVER hard-code creds in this
file — it's committed.

Run:
    python playwright_playground/scripts/05_real_router_probe.py

A real Chromium window opens. You watch it click. Total runtime ~30 s.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from _helpers import Step, new_session_dir
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

# Load .env from the repo root (two levels up from this script).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

BASE_URL = os.environ.get("ROUTER_WEBUI_BASE_URL", "https://192.168.10.1")
USERNAME = os.environ.get("ROUTER_WEBUI_USER", "cisco")
PASSWORD = os.environ.get("ROUTER_WEBUI_PASSWORD", "cisco")


def try_fill_login(page: Page) -> bool:
    """Cisco IOS XE WebUI login form uses Angular with class-based selectors that
    change build-to-build, so we try a sequence of fallbacks from most-stable
    (role/label) to least-stable (CSS class). First one that exists wins.
    Returns True if we successfully filled BOTH fields and submitted.
    """
    user_strategies = [
        lambda: page.get_by_label("Username", exact=False),
        lambda: page.locator("input[name='username']"),
        lambda: page.locator("input[id*='user' i]"),
        lambda: page.locator("input[type='text']").first,
    ]
    pass_strategies = [
        lambda: page.get_by_label("Password", exact=False),
        lambda: page.locator("input[name='password']"),
        lambda: page.locator("input[type='password']"),
    ]
    submit_strategies = [
        lambda: page.get_by_role("button", name="Log In"),
        lambda: page.get_by_role("button", name="Login"),
        lambda: page.get_by_role("button", name="Sign In"),
        lambda: page.locator("button[type='submit']"),
        lambda: page.locator("input[type='submit']"),
    ]

    for find in user_strategies:
        loc = find()
        if loc.count() > 0:
            print(f"  username field matched by: {find.__doc__ or 'strategy'}")
            loc.first.fill(USERNAME)
            break
    else:
        print("  ! could not find username field")
        return False

    for find in pass_strategies:
        loc = find()
        if loc.count() > 0:
            loc.first.fill(PASSWORD)
            break
    else:
        print("  ! could not find password field")
        return False

    for find in submit_strategies:
        loc = find()
        if loc.count() > 0:
            loc.first.click()
            return True
    print("  ! could not find submit button — pressing Enter on password field")
    page.keyboard.press("Enter")
    return True


def report_visible_nav(page: Page, step: Step) -> dict[str, bool]:
    """After login, check which top-level nav items are visible.

    On the C1111 with full priv-15 access we expect Configuration AND
    Administration AND Monitoring (NOT Monitoring-only — that's the §10
    failure mode). Returns a dict so we can assert at the end.
    """
    expected = ["Dashboard", "Monitoring", "Configuration", "Administration", "Troubleshooting"]
    found: dict[str, bool] = {}
    for name in expected:
        # Use locator with text= for case-insensitive contains-match on visible text
        loc = page.locator(f"text=/{name}/i")
        found[name] = loc.count() > 0
        marker = "yes" if found[name] else "MISSING"
        print(f"  nav '{name}': {marker}")
    return found


def main() -> int:
    session = new_session_dir("05_real_router")
    print(f"Session: {session}")
    print(f"Target: {BASE_URL}  (user={USERNAME})")

    if PASSWORD == "REPLACE-ME":
        print("ERROR: .env still has placeholder password — populate it first")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=400)
        context = browser.new_context(
            ignore_https_errors=True,  # the C1111 self-signed cert
            viewport={"width": 1400, "height": 900},
        )
        # Capture console + network errors for the evidence bundle
        page = context.new_page()
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text[:200]}"))
        page.on("pageerror", lambda exc: print(f"  [pageerror] {exc}"))

        step = Step(session)
        nav_findings: dict[str, bool] = {}

        try:
            print("\n1. Open the WebUI URL")
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            # The login page may take a beat to fully render the JS form
            page.wait_for_load_state("networkidle", timeout=15_000)
            step("01-login-page", page)

            print("\n2. Fill credentials and submit")
            ok = try_fill_login(page)
            step("02-credentials-filled", page)
            if not ok:
                print("  FAIL — could not fill the form")
                step("99-form-not-found", page)
                (session / "99-dom.html").write_text(page.content(), encoding="utf-8")
                return 2

            # Wait for the post-login redirect / dashboard to settle.
            print("\n3. Wait for dashboard to load")
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PWTimeout:
                print("  (networkidle timed out — capturing whatever loaded)")
            step("03-after-login", page)
            print(f"  current URL: {page.url}")

            print("\n4. Check which top-level nav items are visible")
            nav_findings = report_visible_nav(page, step)
            step("04-nav-state", page)

            # If Configuration is visible, click it and screenshot — that's the
            # whole point: prove the menu the §10 failure mode hides is back.
            if nav_findings.get("Configuration", False):
                print("\n5. Click Configuration menu")
                try:
                    page.locator("text=/Configuration/i").first.click(timeout=5_000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    step("05-configuration-clicked", page)
                except PWTimeout:
                    print("  (click/load timed out — capturing what we have)")
                    step("05-configuration-timeout", page)
            else:
                print("\n5. SKIP — Configuration menu not visible; capturing DOM for analysis")
                (session / "05-dom-no-config-menu.html").write_text(
                    page.content(), encoding="utf-8"
                )

            print("\nDone.")
            print(f"\nArtifacts: {session}")

            # Exit code reflects whether the §10 failure mode is gone.
            critical_menus = ["Configuration", "Administration"]
            if all(nav_findings.get(m, False) for m in critical_menus):
                print("PASS — Configuration AND Administration menus visible")
                return 0
            print("FAIL — at least one critical menu still missing (see screenshots + DOM dump)")
            return 3
        except Exception as exc:
            print(f"\nEXCEPTION: {type(exc).__name__}: {exc}", file=sys.stderr)
            step("99-exception", page)
            (session / "99-exception-dom.html").write_text(page.content(), encoding="utf-8")
            return 4
        finally:
            # Pause briefly so the user can SEE the final state before the window closes.
            page.wait_for_timeout(2_000)
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
