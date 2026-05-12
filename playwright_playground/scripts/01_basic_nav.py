"""Script 1 — Basic navigation.

What this teaches:
  - launching headed Chromium with slow_mo so you can watch
  - role-based locators (get_by_role, get_by_label) — the preferred strategy
  - clicking links and waiting for load
  - screenshots between every step

Run (in two terminals):
    python playwright_playground/serve.py
    python playwright_playground/scripts/01_basic_nav.py
"""

from __future__ import annotations

from _helpers import BASE_URL, Step, new_session_dir
from playwright.sync_api import sync_playwright


def main() -> None:
    session = new_session_dir("01_basic_nav")
    print(f"Session: {session}")

    with sync_playwright() as p:
        # headless=False shows the browser window. slow_mo=400 inserts a 400ms
        # pause between every action — invaluable for watching what's happening.
        browser = p.chromium.launch(headless=False, slow_mo=400)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        step = Step(session)

        try:
            print("1. Opening login page")
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")
            step("01-login-empty", page)

            print("2. Filling credentials (role-based locators)")
            page.get_by_label("Username").fill("admin")
            page.get_by_label("Password").fill("admin")
            step("02-credentials-filled", page)

            print("3. Clicking the Log in button")
            page.get_by_role("button", name="Log in").click()
            # Wait for the navigation triggered by the login redirect.
            page.wait_for_url("**/dashboard.html")
            page.wait_for_load_state("networkidle")
            step("03-dashboard", page)

            print("4. Navigating to VLANs via the sidebar")
            page.get_by_role("link", name="VLANs").click()
            page.wait_for_url("**/vlan-list.html")
            page.wait_for_load_state("networkidle")
            step("04-vlan-list", page)

            print("Done")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
