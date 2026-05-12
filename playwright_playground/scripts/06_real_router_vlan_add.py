"""Script 6 — Real-router VLAN add via Playwright.

Extends the cert/login probe (05) by navigating all the way into the VLAN
creation form and filling it out.  By default this script runs in DRY_RUN
mode — it fills the form but clicks Cancel instead of Save.  Set
PLAYWRIGHT_DRY_RUN=false in .env (or in the shell) to actually commit the
VLAN to the router.

Navigation path (IOS XE 17.x primary):
    Login → Configuration → Layer 2 → VLAN → Add → fill → Cancel | Save

Fallback paths tried in order when the primary path fails:
    Configuration → LAN → VLAN
    Configuration → (any visible link whose text contains "vlan")

Every step produces a PNG in artifacts/06_real_router_vlan_<timestamp>/.
Failures also dump a DOM snapshot (.html) so you can read the real selector
names and adjust the fallback lists below.

Exit codes:
    0  — VLAN form filled + save confirmed (or dry-run back-out succeeded)
    1  — .env not populated
    2  — login form not found / could not submit
    3  — could not navigate to the VLAN page after three path attempts
    4  — VLAN form fields not found
    5  — unhandled exception

Run:
    cd playwright_playground
    python scripts/06_real_router_vlan_add.py
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from _helpers import Step, new_session_dir
from dotenv import load_dotenv
from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

BASE_URL   = os.environ.get("ROUTER_WEBUI_BASE_URL", "https://192.168.10.1")
USERNAME   = os.environ.get("ROUTER_WEBUI_USER",     "cisco")
PASSWORD   = os.environ.get("ROUTER_WEBUI_PASSWORD", "REPLACE-ME")
VLAN_ID    = os.environ.get("VLAN_ID",               "99")
VLAN_NAME  = os.environ.get("VLAN_NAME",             "TEST-PLAYWRIGHT")
DRY_RUN    = os.environ.get("PLAYWRIGHT_DRY_RUN", "true").lower() not in ("false", "0", "no")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_visible(candidates: list[Locator]) -> Locator | None:
    """Return the first locator in the list that exists in the DOM."""
    for loc in candidates:
        with contextlib.suppress(Exception):
            if loc.count() > 0:
                return loc
    return None


def _dump_dom(page: Page, session: Path, label: str) -> None:
    dest = session / f"{label}-dom.html"
    dest.write_text(page.content(), encoding="utf-8")
    print(f"  -> DOM dump: {dest.name}")


def _wait_idle(page: Page, timeout_ms: int = 10_000) -> None:
    with contextlib.suppress(PWTimeout):
        page.wait_for_load_state("networkidle", timeout=timeout_ms)


# ---------------------------------------------------------------------------
# Step 1 — Login  (same multi-strategy approach as script 05)
# ---------------------------------------------------------------------------

def do_login(page: Page, step: Step, session: Path) -> bool:
    print("\n1. Open WebUI")
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
    _wait_idle(page, 15_000)
    step("01-login-page", page)

    print("\n2. Fill credentials")
    user_loc = _first_visible([
        page.get_by_label("Username", exact=False),
        page.locator("input[name='username']"),
        page.locator("input[id*='user' i]"),
        page.locator("input[type='text']").first,
    ])
    pass_loc = _first_visible([
        page.get_by_label("Password", exact=False),
        page.locator("input[name='password']"),
        page.locator("input[type='password']"),
    ])
    submit_loc = _first_visible([
        page.get_by_role("button", name="Log In"),
        page.get_by_role("button", name="Login"),
        page.get_by_role("button", name="Sign In"),
        page.locator("button[type='submit']"),
        page.locator("input[type='submit']"),
    ])

    if not user_loc or not pass_loc:
        print("  ! login fields not found — dumping DOM")
        _dump_dom(page, session, "02-login-fields-missing")
        step("02-login-fail", page)
        return False

    user_loc.first.fill(USERNAME)
    pass_loc.first.fill(PASSWORD)
    step("02-credentials-filled", page)

    if submit_loc:
        submit_loc.first.click()
    else:
        print("  (no submit button — pressing Enter)")
        page.keyboard.press("Enter")

    print("\n3. Wait for dashboard")
    _wait_idle(page, 30_000)
    step("03-after-login", page)
    print(f"  URL: {page.url}")
    return True


# ---------------------------------------------------------------------------
# Step 2 — Navigate to the VLAN page
# ---------------------------------------------------------------------------

def _click_first_match(page: Page, candidates: list[str], timeout_ms: int = 5_000) -> bool:
    """Try each CSS/text selector in order; click the first that is visible."""
    for sel in candidates:
        with contextlib.suppress(PWTimeout, Exception):
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                return True
    return False


def navigate_to_vlan_page(page: Page, step: Step, session: Path) -> bool:
    """Try three navigation paths to reach the VLAN list/database page.

    Path A (primary): Configuration → Layer 2 → VLAN
    Path B (fallback): Configuration → LAN → VLAN
    Path C (last resort): any visible nav link whose text contains "vlan"
    """
    print("\n4. Click Configuration in top nav")
    cfg_candidates = [
        "text=/^Configuration$/i",
        "a:has-text('Configuration')",
        "span:has-text('Configuration')",
        "[routerlink*='configuration' i]",
        "[href*='configuration' i]",
    ]
    if not _click_first_match(page, cfg_candidates):
        print("  ! Configuration menu not found — dumping DOM")
        _dump_dom(page, session, "04-no-config-menu")
        step("04-config-missing", page)
        return False

    _wait_idle(page, 10_000)
    step("04-configuration-clicked", page)

    # ---- Path A: Layer 2 → VLAN ----------------------------------------
    print("\n5a. Path A — trying Layer 2 submenu")
    l2_candidates = [
        "text=/layer.?2/i",
        "a:has-text('Layer 2')",
        "span:has-text('Layer 2')",
        "[routerlink*='layer2' i]",
        "[routerlink*='layer-2' i]",
    ]
    if _click_first_match(page, l2_candidates):
        _wait_idle(page, 8_000)
        step("05a-layer2-clicked", page)

        vlan_sub_candidates = [
            "text=/^VLAN$/i",
            "text=/vlan.database/i",
            "text=/vlan.config/i",
            "a:has-text('VLAN')",
            "span:has-text('VLAN')",
            "[routerlink*='vlan' i]",
        ]
        if _click_first_match(page, vlan_sub_candidates):
            _wait_idle(page, 10_000)
            step("05a-vlan-page", page)
            print("  Path A succeeded")
            return True
        print("  Path A: Layer 2 found but no VLAN sub-item visible")
        _dump_dom(page, session, "05a-layer2-no-vlan")

    # ---- Path B: LAN → VLAN --------------------------------------------
    print("\n5b. Path B — trying LAN submenu")
    # Re-open Configuration since sub-menus may have collapsed
    _click_first_match(page, cfg_candidates)
    _wait_idle(page, 5_000)

    lan_candidates = [
        "text=/^LAN$/i",
        "a:has-text('LAN')",
        "span:has-text('LAN')",
        "[routerlink*='/lan' i]",
    ]
    if _click_first_match(page, lan_candidates):
        _wait_idle(page, 8_000)
        step("05b-lan-clicked", page)

        vlan_sub_candidates = [
            "text=/vlan/i",
            "a:has-text('VLAN')",
            "[routerlink*='vlan' i]",
        ]
        if _click_first_match(page, vlan_sub_candidates):
            _wait_idle(page, 10_000)
            step("05b-vlan-page", page)
            print("  Path B succeeded")
            return True
        print("  Path B: LAN found but no VLAN sub-item")
        _dump_dom(page, session, "05b-lan-no-vlan")

    # ---- Path C: any nav link containing "vlan" -------------------------
    print("\n5c. Path C — scanning all nav links for 'vlan'")
    step("05c-scan", page)
    any_vlan = page.locator("a:has-text('VLAN'), button:has-text('VLAN'), span:has-text('VLAN')")
    if any_vlan.count() > 0:
        any_vlan.first.click()
        _wait_idle(page, 10_000)
        step("05c-vlan-page", page)
        print("  Path C succeeded")
        return True

    print("  ! all three nav paths failed — see DOM dumps in session folder")
    _dump_dom(page, session, "05c-all-paths-failed")
    return False


# ---------------------------------------------------------------------------
# Step 3 — Click Add, fill the VLAN form, cancel or save
# ---------------------------------------------------------------------------

def fill_vlan_form(page: Page, step: Step, session: Path) -> bool:
    print(f"\n6. Click Add (DRY_RUN={DRY_RUN}, VLAN_ID={VLAN_ID}, VLAN_NAME={VLAN_NAME})")
    add_candidates = [
        "button:has-text('Add')",
        "button:has-text('+')",
        "button:has-text('New')",
        "button:has-text('Create')",
        "a:has-text('Add')",
        "[aria-label*='add' i]",
        ".add-btn",
    ]
    if not _click_first_match(page, add_candidates):
        print("  ! Add button not found — dumping DOM")
        _dump_dom(page, session, "06-no-add-button")
        step("06-add-missing", page)
        return False

    _wait_idle(page, 10_000)
    step("06-add-form-opened", page)

    print("\n7. Fill VLAN ID")
    vlan_id_loc = _first_visible([
        page.get_by_label("VLAN ID", exact=False),
        page.locator("input[placeholder*='VLAN ID' i]"),
        page.locator("input[placeholder*='vlan' i]"),
        page.locator("input[id*='vlan' i]"),
        page.locator("input[name*='vlan' i]"),
        page.locator("input[type='number']").first,
        page.locator("input[type='text']").first,
    ])
    if not vlan_id_loc:
        print("  ! VLAN ID field not found — dumping DOM")
        _dump_dom(page, session, "07-no-vlan-id-field")
        step("07-vlan-id-missing", page)
        return False
    vlan_id_loc.first.triple_click()   # select-all then replace
    vlan_id_loc.first.fill(VLAN_ID)
    print(f"  filled VLAN ID = {VLAN_ID}")

    print("\n8. Fill VLAN Name")
    vlan_name_loc = _first_visible([
        page.get_by_label("VLAN Name", exact=False),
        page.get_by_label("Name", exact=False),
        page.locator("input[placeholder*='name' i]"),
        page.locator("input[id*='name' i]"),
        page.locator("input[name*='name' i]"),
        page.locator("input[type='text']").nth(1),  # second text input after VLAN ID
    ])
    if vlan_name_loc:
        vlan_name_loc.first.triple_click()
        vlan_name_loc.first.fill(VLAN_NAME)
        print(f"  filled VLAN Name = {VLAN_NAME}")
    else:
        print("  (no VLAN Name field found — skipping, some IOS XE builds omit it)")

    step("08-form-filled", page)

    # ---- Save or Cancel ------------------------------------------------
    if DRY_RUN:
        print("\n9. DRY_RUN=true — clicking Cancel / Close (not saving)")
        cancel_candidates = [
            "button:has-text('Cancel')",
            "button:has-text('Close')",
            "button:has-text('Back')",
            "[aria-label*='cancel' i]",
            "[aria-label*='close' i]",
        ]
        if not _click_first_match(page, cancel_candidates):
            print("  (no Cancel button found — pressing Escape)")
            page.keyboard.press("Escape")
        _wait_idle(page, 8_000)
        step("09-dry-run-cancelled", page)
        print("\n  DRY_RUN complete — form filled but NOT saved.")
        print(f"  Set PLAYWRIGHT_DRY_RUN=false in .env to actually create VLAN {VLAN_ID}.")
    else:
        print(f"\n9. LIVE — clicking Save to create VLAN {VLAN_ID}")
        save_candidates = [
            "button:has-text('Save')",
            "button:has-text('Apply')",
            "button:has-text('OK')",
            "button:has-text('Add')",
            "button[type='submit']",
            "input[type='submit']",
        ]
        if not _click_first_match(page, save_candidates):
            print("  ! Save button not found — dumping DOM")
            _dump_dom(page, session, "09-no-save-button")
            step("09-save-missing", page)
            return False
        _wait_idle(page, 15_000)
        step("09-after-save", page)
        print(f"  VLAN {VLAN_ID} save submitted — check screenshot for confirmation")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if PASSWORD == "REPLACE-ME":
        print("ERROR: .env still has placeholder password. Populate ROUTER_WEBUI_PASSWORD first.")
        return 1

    session = new_session_dir("06_real_router_vlan")
    print(f"Session : {session}")
    print(f"Target  : {BASE_URL}  (user={USERNAME})")
    print(f"VLAN    : id={VLAN_ID}  name={VLAN_NAME}  dry_run={DRY_RUN}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=500,
            args=["--ignore-certificate-errors"],   # belt-and-suspenders for the self-signed cert
        )
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()
        page.on("console",   lambda m: print(f"  [console.{m.type}] {m.text[:200]}"))
        page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))

        step = Step(session)

        try:
            if not do_login(page, step, session):
                return 2

            if not navigate_to_vlan_page(page, step, session):
                return 3

            if not fill_vlan_form(page, step, session):
                return 4

            print(f"\nDone.  Artifacts: {session}")
            return 0

        except Exception as exc:
            print(f"\nEXCEPTION: {type(exc).__name__}: {exc}", file=sys.stderr)
            with contextlib.suppress(Exception):
                step("99-exception", page)
                _dump_dom(page, session, "99-exception")
            return 5
        finally:
            page.wait_for_timeout(2_500)
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
