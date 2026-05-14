"""Script 7 — interactive inspection of the post-login dashboard.

Day 5 found that HostnamePage.goto() can't locate the 'Administration' menu
even though script 05 reported it visible. This script logs in and dumps
everything we know about the page, then PAUSES so you can poke at the live
Chromium with devtools (F12).

Run:
    cd <worktree>
    .venv\\Scripts\\Activate.ps1
    python playwright_playground/scripts/07_inspect_dashboard.py

What you'll get:
- post-login URL
- a 2 KB excerpt of body text (anchors, buttons, headings — copy whatever
  matches 'Administration' and pass it back)
- the inner text + href of the first ~30 anchor elements
- a full DOM dump saved to artifacts/07_inspect_<timestamp>/dom.html
- the Chromium window stays open until you press Enter in the terminal
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from backend.webui_agent.browser import webui_browser  # noqa: E402
from backend.webui_agent.login import login  # noqa: E402


def main() -> int:
    if os.environ.get("ROUTER_WEBUI_PASSWORD", "") in ("", "REPLACE-ME"):
        print("ERROR: ROUTER_WEBUI_PASSWORD not set in .env")
        return 1

    artifacts = REPO_ROOT / "artifacts" / "07_inspect"
    artifacts.mkdir(parents=True, exist_ok=True)

    print("Launching headed Chromium…")
    with webui_browser(slow_mo=200) as page:
        print("Logging in…")
        if not login(page):
            print("LOGIN FAILED — see uvicorn / structlog output above")
            return 2

        # Give Angular a moment to render
        page.wait_for_timeout(5000)

        print(f"\nURL after login: {page.url}\n")

        # ------------- Body text excerpt ----------------------------
        print("=" * 60)
        print("Body text (first 2000 chars):")
        print("=" * 60)
        try:
            body_text = page.locator("body").inner_text()
            print(body_text[:2000])
        except Exception as exc:
            print(f"(failed to read body text: {exc})")

        # ------------- Anchor inventory -----------------------------
        print("\n" + "=" * 60)
        print("First 30 anchors on the page:")
        print("=" * 60)
        try:
            anchors = page.locator("a").all()
            for i, a in enumerate(anchors[:30]):
                try:
                    txt = (a.inner_text() or "").replace("\n", " ").strip()[:60]
                    href = a.get_attribute("href") or ""
                    rlink = a.get_attribute("routerlink") or ""
                    print(f"  [{i:02d}] text={txt!r}  href={href!r}  routerlink={rlink!r}")
                except Exception:
                    pass
        except Exception as exc:
            print(f"(anchor scan failed: {exc})")

        # ------------- Buttons inventory ----------------------------
        print("\n" + "=" * 60)
        print("First 20 buttons on the page:")
        print("=" * 60)
        try:
            buttons = page.locator("button").all()
            for i, b in enumerate(buttons[:20]):
                try:
                    txt = (b.inner_text() or "").replace("\n", " ").strip()[:60]
                    print(f"  [{i:02d}] text={txt!r}")
                except Exception:
                    pass
        except Exception as exc:
            print(f"(button scan failed: {exc})")

        # ------------- Search specifically for "Administration" -----
        print("\n" + "=" * 60)
        print("Locator counts for 'Administration':")
        print("=" * 60)
        for label, selector in [
            ("text=Administration", "text=Administration"),
            ("a:has-text('Administration')", "a:has-text('Administration')"),
            ("span:has-text('Administration')", "span:has-text('Administration')"),
            ("[routerlink*='administration' i]", "[routerlink*='administration' i]"),
            ("[href*='administration' i]", "[href*='administration' i]"),
            ("text=Configuration", "text=Configuration"),
            ("text=Dashboard", "text=Dashboard"),
        ]:
            try:
                count = page.locator(selector).count()
                print(f"  {label:55s}  count = {count}")
            except Exception as exc:
                print(f"  {label:55s}  ERROR: {exc}")

        # ------------- Full DOM dump --------------------------------
        dom_path = artifacts / "dom.html"
        try:
            dom_path.write_text(page.content(), encoding="utf-8")
            print(f"\nFull DOM saved to: {dom_path}")
        except Exception as exc:
            print(f"\nDOM dump failed: {exc}")

        # ------------- Pause for manual inspection ------------------
        print("\n" + "=" * 60)
        print("Chromium is now paused. Open F12 devtools to inspect the page.")
        print("Press Enter in this terminal when you're done — Chromium will close.")
        print("=" * 60)
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
