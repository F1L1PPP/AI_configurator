"""Script 3 — Submit, then verify by reading state back.

What this teaches:
  - the verification pattern from PROJECT_PLAN §4.3 / smoke scenarios §6
  - asserting a row appears in a table after a write
  - using locator.count() and locator.is_visible() to verify
  - the test fails *loudly* with a clear error if verification fails

Mirrors the real-world rule: a WebUI write isn't trusted until verified.

Run (in two terminals):
    python playwright_playground/serve.py
    python playwright_playground/scripts/03_verify.py
"""

from __future__ import annotations

import sys

from _helpers import BASE_URL, Step, new_session_dir
from playwright.sync_api import expect, sync_playwright

VLAN_ID = "42"
VLAN_NAME = "ENGINEERING"
VLAN_INTERFACE = "GigabitEthernet0/0/2"


def main() -> int:
    session = new_session_dir("03_verify")
    print(f"Session: {session}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        step = Step(session)

        try:
            # === phase 1: act ===
            page.goto(BASE_URL)
            page.get_by_label("Username").fill("admin")
            page.get_by_label("Password").fill("admin")
            page.get_by_role("button", name="Log in").click()
            page.wait_for_url("**/dashboard.html")

            page.get_by_role("link", name="VLANs").click()
            page.wait_for_url("**/vlan-list.html")
            page.wait_for_load_state("networkidle")

            page.get_by_role("button", name="+ Add VLAN").click()
            page.wait_for_url("**/vlan-add.html")

            page.get_by_label("VLAN ID").fill(VLAN_ID)
            page.get_by_label("Name").fill(VLAN_NAME)
            page.get_by_label("Interface").select_option(label=VLAN_INTERFACE)
            page.get_by_label("Mode").select_option(label="Access")
            step("01-pre-save", page)

            page.get_by_role("button", name="Save").click()
            page.wait_for_url("**/vlan-list.html?**")
            page.wait_for_load_state("networkidle")
            step("02-post-save", page)

            # === phase 2: verify ===
            print(f"Verifying VLAN {VLAN_ID} appears in the list")

            # Wait for the table to render (the script delays loading by 500ms)
            page.wait_for_selector("table tbody tr", timeout=5000)

            # Assertion 1: the success banner mentions the new VLAN id
            banner = page.get_by_role("status").first
            expect(banner).to_contain_text(f"VLAN {VLAN_ID}")

            # Assertion 2: a table row with both id and name exists
            row = page.locator("tbody tr", has_text=VLAN_NAME)
            expect(row).to_be_visible()
            expect(row).to_contain_text(VLAN_ID)
            expect(row).to_contain_text(VLAN_INTERFACE.replace("GigabitEthernet", "Gi"))

            print(f"  ok: banner mentions VLAN {VLAN_ID}")
            print(f"  ok: table row with {VLAN_NAME} on {VLAN_INTERFACE} exists")
            step("03-verified", page)

            print("\nPASS — write verified")
            return 0
        except Exception as exc:
            print(f"\nFAIL — verification did not hold: {exc}", file=sys.stderr)
            step("99-fail", page)
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
