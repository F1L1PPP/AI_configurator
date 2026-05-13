"""Script 4 — Error handling (no auto-retry on write).

What this teaches:
  - what happens when a selector goes stale or wrong
  - the mandatory evidence-on-error pattern: screenshot + DOM dump + abort
  - the hard rule from CLAUDE.md #5: NEVER auto-retry a write
  - exit non-zero on failure so CI / the orchestrator know

Two scenarios:
  - read-side failure (bad selector) — picks the wrong nav link
  - write-side failure (form submit selector wrong) — would have caused a half-write

Run:
    python playwright_playground/serve.py
    python playwright_playground/scripts/04_error_handling.py
"""

from __future__ import annotations

import sys

from _helpers import BASE_URL, Step, new_session_dir
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright


def save_evidence(step: object, page: object, label: str) -> None:  # type: ignore[no-untyped-def]
    """The four artifacts we always capture on WebUI failure."""
    # 1. Screenshot of the failed state
    step(f"99-{label}-screenshot", page)  # type: ignore[operator]

    # 2. DOM dump (page.content() — the rendered HTML)
    dump_path = step.dir / f"99-{label}-dom.html"  # type: ignore[attr-defined]
    dump_path.write_text(page.content(), encoding="utf-8")  # type: ignore[attr-defined]
    print(f"  -> DOM dump  {dump_path.name}")

    # 3. URL at time of failure
    print(f"  -> URL       {page.url}")  # type: ignore[attr-defined]

    # 4. Browser console messages (collected by the listener attached at launch)


def main() -> int:
    session = new_session_dir("04_error_handling")
    print(f"Session: {session}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        # Capture browser console output for the evidence bundle
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text}"))

        step = Step(session)

        try:
            print("1. Login (this works)")
            page.goto(BASE_URL)
            page.get_by_label("Username").fill("admin")
            page.get_by_label("Password").fill("admin")
            page.get_by_role("button", name="Log in").click()
            page.wait_for_url("**/dashboard.html")
            page.wait_for_load_state("networkidle")
            step("01-dashboard", page)

            print("2. Trying a deliberately wrong selector — get_by_role('link', name='Networks')")
            print("   (this should fail with a clear timeout — the link doesn't exist)")

            try:
                page.get_by_role("link", name="Networks").click(timeout=3000)
            except PWTimeout as exc:
                print(f"\n  EXPECTED TIMEOUT: {type(exc).__name__}")
                print("  Per CLAUDE.md hard rule #5, we DO NOT retry. We save evidence and abort.")
                save_evidence(step, page, "missing-networks-link")
                return 2

            # If we got here, the selector unexpectedly worked
            print("Unexpected: the wrong selector worked. Bailing.", file=sys.stderr)
            return 1

        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
