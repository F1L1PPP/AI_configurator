"""Human-driven WebUI catalog recorder.

Usage
-----
Run this script directly; it opens a headed Chromium, logs in, and then
auto-captures URL + ``describe_page`` output on every navigation.  Filip
navigates manually — the script just watches ``page.url`` and records.
Press Ctrl+C in this terminal to save and exit.

Output
------
*  ``knowledge_base/webui-catalog/current.json``  — the blessed catalog that
   Phase 5's navigation-map injection step reads.
*  ``artifacts/webui-catalog/catalog-<YYYYMMDD-HHMMSS>-manual.json`` — a
   timestamped copy for audit / traceability.

Why this exists
---------------
``scripts/catalog_webui_elements.py`` auto-walks the Angular sidebar but is
fragile against nested-overlay sections.  This script lets Filip (who has
domain knowledge) drive while the system records, with no manual breadcrumb
labeling required.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Make the repo root importable when the script is run directly.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.core.settings import get_settings  # noqa: E402
from backend.webui_agent.browser import webui_browser  # noqa: E402
from backend.webui_agent.login import login, start_keepalive  # noqa: E402
from backend.webui_agent.semantic_dom import describe_page  # noqa: E402

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

ANGULAR_SETTLE_S: float = 2.0  # Wait after URL change before describe_page
POLL_INTERVAL_S: float = 0.5  # How often to poll page.url
MAX_PAGES: int = 200  # Defensive cap — prevents runaway overnight run


# ---------------------------------------------------------------------------
# Git helper (copy kept here so the script stays self-contained)
# ---------------------------------------------------------------------------


def _git_short_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def _capture_if_new(
    page: object,
    pages: list[dict],
    visited_urls: set[str],
) -> None:
    """Capture the current page if its URL has not been visited yet.

    On ``describe_page`` failure a stub entry is still recorded so we
    know Filip visited the URL — the ``describe_failed`` key signals the
    failure to downstream consumers.
    """
    try:
        url = page.url  # type: ignore[attr-defined]
    except Exception as exc:
        print(f"  WARN: cannot read page.url: {exc}")
        return
    if url in visited_urls:
        return
    visited_urls.add(url)
    try:
        view, _locator_map = describe_page(page)  # type: ignore[arg-type]
    except Exception as exc:
        print(f"  WARN: describe_page failed for {url}: {exc}")
        # Record a stub so Phase 5 can see Filip visited even if describe failed
        pages.append(
            {
                "url": url,
                "title": "",
                "elements": [],
                "modals": [],
                "errors": [],
                "captured_at": datetime.now(UTC).isoformat(),
                "describe_failed": str(exc),
            }
        )
        return
    pages.append(
        {
            "url": url,
            "title": view.get("title", ""),
            "view_id": view.get("view_id"),
            "elements": view.get("elements", []),
            "modals": view.get("modals", []),
            "errors": view.get("errors", []),
            "captured_at": datetime.now(UTC).isoformat(),
        }
    )
    n_el = len(view.get("elements", []))
    print(f"  Captured #{len(pages)}: {url}  ({n_el} elements)")


def _save_catalog(
    pages: list[dict],
    settings: object,
    *,
    artifacts_dir: Path | None = None,
    blessed_dir: Path | None = None,
) -> int:
    """Write catalog to both the artifacts dir and the blessed path.

    ``artifacts_dir`` and ``blessed_dir`` are injected for tests; callers
    that omit them get paths derived from ``settings``.
    """
    catalog = {
        "catalog_timestamp": datetime.now(UTC).isoformat(),
        "router_host": getattr(settings, "router_host", ""),
        "git_commit": _git_short_sha(),
        "recorder": "manual",
        "summary": {
            "total_pages": len(pages),
            "total_elements": sum(len(p.get("elements", [])) for p in pages),
        },
        "pages": pages,
    }

    if artifacts_dir is None:
        artifacts_dir = Path(getattr(settings, "artifacts_dir", "artifacts")) / "webui-catalog"
    if blessed_dir is None:
        blessed_dir = Path("knowledge_base") / "webui-catalog"

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    blessed_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    artifacts_path = artifacts_dir / f"catalog-{ts}-manual.json"
    blessed_path = blessed_dir / "current.json"

    catalog_json = json.dumps(catalog, indent=2, default=str)
    artifacts_path.write_text(catalog_json, encoding="utf-8")
    blessed_path.write_text(catalog_json, encoding="utf-8")

    s = catalog["summary"]
    print(f"\n=> Saved {s['total_pages']} pages, {s['total_elements']} elements total")
    print(f"   Artifact: {artifacts_path}")
    print(f"   Blessed:  {blessed_path}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    settings = get_settings()
    pages: list[dict] = []
    visited_urls: set[str] = set()

    print(f"=> Opening Cisco WebUI at {settings.router_host}")
    print("=> Click through each page you want catalogued.")
    print("=> Press Ctrl+C in this terminal to save and exit.\n")

    with webui_browser() as page:
        login(page)

        # Keepalive useful here: Filip may pause on a page for several minutes
        # while thinking about what to check next.  start_keepalive docs note
        # it must NOT overlap with active clicks; since Filip is driving, the
        # background nudge fires only during genuine idle windows — acceptable.
        _stop_keepalive = None
        try:
            _stop_keepalive = start_keepalive(page)
        except Exception as exc:
            print(f"  (keepalive unavailable: {exc})")

        # Capture the post-login landing page first
        time.sleep(ANGULAR_SETTLE_S)
        _capture_if_new(page, pages, visited_urls)

        try:
            last_url = page.url
            while True:
                if len(pages) >= MAX_PAGES:
                    print(f"\n=> MAX_PAGES ({MAX_PAGES}) reached — saving now.")
                    break
                try:
                    current_url = page.url
                except Exception:
                    # Browser closed by Filip — exit cleanly
                    break
                if current_url != last_url:
                    time.sleep(ANGULAR_SETTLE_S)  # Wait for Angular paint
                    _capture_if_new(page, pages, visited_urls)
                    last_url = current_url
                time.sleep(POLL_INTERVAL_S)
        except KeyboardInterrupt:
            print("\n=> Ctrl+C — finalizing catalog...")
        finally:
            if _stop_keepalive is not None:
                with contextlib.suppress(Exception):
                    _stop_keepalive.set()

    return _save_catalog(pages, settings)


if __name__ == "__main__":
    raise SystemExit(main())
