"""Script 2 — Form submission.

What this teaches:
  - filling multiple input types (number, text, select dropdown)
  - dropdown selection by visible label OR value
  - waiting for navigation triggered by form submit
  - reading state back from the page after the redirect

Mirrors the Day 8 WebUI VLAN add flow.

Run (in two terminals):
    python playwright_playground/serve.py
    python playwright_playground/scripts/02_form_submit.py
"""

from __future__ import annotations

from _helpers import BASE_URL, Step, new_session_dir
from playwright.sync_api import sync_playwright

VLAN_ID = "30"
VLAN_NAME = "OFFICE"
VLAN_INTERFACE = "GigabitEthernet0/0/1"
VLAN_MODE = "Access"


def main() -> None:
    session = new_session_dir("02_form_submit")
    print(f"Session: {session}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=400)
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 800})
        page = context.new_page()
        step = Step(session)

        try:
            print("1. Login")
            page.goto(BASE_URL)
            page.get_by_label("Username").fill("admin")
            page.get_by_label("Password").fill("admin")
            page.get_by_role("button", name="Log in").click()
            page.wait_for_url("**/dashboard.html")
            page.wait_for_load_state("networkidle")
            step("01-dashboard", page)

            print("2. Navigate to VLAN list")
            page.get_by_role("link", name="VLANs").click()
            page.wait_for_url("**/vlan-list.html")
            page.wait_for_load_state("networkidle")
            step("02-vlan-list-empty", page)

            print("3. Click '+ Add VLAN'")
            page.get_by_role("button", name="+ Add VLAN").click()
            page.wait_for_url("**/vlan-add.html")
            page.wait_for_load_state("networkidle")
            step("03-add-form-empty", page)

            print(f"4. Fill form: id={VLAN_ID} name={VLAN_NAME} interface={VLAN_INTERFACE} mode={VLAN_MODE}")
            page.get_by_label("VLAN ID").fill(VLAN_ID)
            page.get_by_label("Name").fill(VLAN_NAME)
            # select_option accepts label= for the visible text or value= for the value attribute
            page.get_by_label("Interface").select_option(label=VLAN_INTERFACE)
            page.get_by_label("Mode").select_option(label=VLAN_MODE)
            step("04-add-form-filled", page)

            print("5. Submit (Save) — page will redirect back to the list")
            page.get_by_role("button", name="Save").click()
            # The submit handler does a fake 800ms delay then sets window.location;
            # wait_for_url is the right primitive — it waits until the URL matches.
            page.wait_for_url("**/vlan-list.html?**")
            page.wait_for_load_state("networkidle")
            step("05-after-save", page)

            print("Done — the new VLAN row should be visible above the empty state was.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
