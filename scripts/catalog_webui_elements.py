#!/usr/bin/env python
"""Catalog the Cisco WebUI for AI grounding + Playwright reference.

Walks the sidebar tree on the real C1111 via the Phase 4 slice 2 generic
driver: clicks each top-level item, discovers the newly-revealed leaf links,
visits each one to capture its URL + semantic-DOM view, and saves the
aggregated result to `artifacts/webui-catalog/` and
`knowledge_base/webui-catalog/current.json` (the "blessed" snapshot the
backend reads for Phase 5 navigation planning).

Read-only against the router. The walker never fills forms, never clicks
Apply / Save / Submit, and skips a hardcoded deny-list of destructive names
as a defense-in-depth measure (the executor's own deny-list is a second layer
on top).

USAGE
    .venv\\Scripts\\python.exe scripts\\catalog_webui_elements.py

OUTPUT
    artifacts/webui-catalog/catalog-<UTC-timestamp>.json    machine-readable
    artifacts/webui-catalog/catalog-<UTC-timestamp>.md      human-readable
    knowledge_base/webui-catalog/current.json               blessed snapshot

Requires ROUTER_HOST + ROUTER_WEBUI_USER + ROUTER_WEBUI_PASSWORD in
your .env. Headed Chromium opens so you can watch.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make `backend` importable when running this script directly via the
# worktree venv (no `pip install -e .` applied). Path is repo-root,
# parent of this scripts/ dir.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.core.settings import get_settings  # noqa: E402
from backend.orchestration.confirmations import (  # noqa: E402
    approve_action,
    propose_action,
)
from backend.webui_agent.generic_driver import (  # noqa: E402
    close_all_sessions,
    webui_act_by_intent,
    webui_describe_page,
    webui_open,
)

# ----- Configuration --------------------------------------------------------

# Set True for CI / headless re-runs. Default False so Filip can watch.
HEADLESS = False

# Seconds to wait after navigation / click before re-describing. Angular
# needs a moment to render after a hash-route change or sidebar click.
ANGULAR_SETTLE_S = 2.0

# Sidebar top-level items to click in order.
TOP_LEVEL_ITEMS = [
    "Dashboard",
    "Monitoring",
    "Configuration",
    "Administration",
    "Licensing",
    "Troubleshooting",
]

# Names containing any of these substrings are NEVER clicked by the walker.
# Defense in depth — the sensitive-text deny-list in _do_act_by_intent (QW3)
# also catches the executor case, but the walker should skip them upfront
# so we don't waste a click + get a confusing failure.
_CATALOG_SAFETY_DENY: frozenset[str] = frozenset(
    {
        # Destructive (overlap with the executor deny-list)
        "factory reset",
        "reboot",
        "restart",
        "delete",
        "remove",
        "restore configuration",
        "disable http server",
        "clear configuration",
        # Form-action names that shouldn't be auto-clicked during navigation walk
        "apply",
        "save",
        "submit",
        "ok",
        "confirm",
    }
)

# Cap how many leaves we visit under each top-level. The C1111 WebUI exposes
# ~5-10 leaves per category; 50 is enough to never truncate. Defensive against
# misclassification picking up random page links.
_MAX_LEAVES_PER_TOP = 50

# Cap total walk time. Real router walks take 5-10 min; cap at 20 to fail
# loud rather than hanging.
_MAX_WALK_SECONDS = 1200


# ----- Safety helper --------------------------------------------------------


def _is_safe_to_click(name: str | None) -> bool:
    """Return True if ``name`` is safe to click during the sidebar walk.

    Rejects empty / whitespace names and any name whose lowercase form
    contains a phrase from ``_CATALOG_SAFETY_DENY``.
    """
    if not name or not name.strip():
        return False
    n = name.lower()
    return not any(phrase in n for phrase in _CATALOG_SAFETY_DENY)


# ----- Git helper -----------------------------------------------------------


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


# ----- Sidebar walker (injectable deps for testability) ---------------------


def walk_sidebar(
    open_fn: Callable[[str, str], dict[str, Any]],
    describe_fn: Callable[[str], dict[str, Any]],
    act_by_intent_fn: Callable[[str, dict[str, Any], str], dict[str, Any]],
    session_id: str,
    action_id: str,
    *,
    settle_s: float = ANGULAR_SETTLE_S,
    top_level_items: Sequence[str] = TOP_LEVEL_ITEMS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drive the sidebar walk and return ``(pages, failures)``.

    Args:
        open_fn:           webui_open-compatible callable (path, action_id).
        describe_fn:       webui_describe_page-compatible callable (session_id).
        act_by_intent_fn:  webui_act_by_intent-compatible callable
                           (session_id, intent, action_id).
        session_id:        the live session to drive.
        action_id:         the approved action_id for HITL gates.
        settle_s:          seconds to sleep after each click / navigation.
        top_level_items:   ordered list of top-level sidebar names to click.

    Returns:
        pages:    list of page-entry dicts (breadcrumb, url, title, …).
        failures: list of failure dicts (top, leaf, error, message).
    """
    pages: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    visited_urls: set[str] = set()

    walk_start = time.monotonic()

    # --- Step 3: capture the initial dashboard view (reference eids) --------
    r_dash = describe_fn(session_id)
    if "error" in r_dash:
        # If we can't even describe the dashboard, nothing will work.
        failures.append(
            {
                "top": "dashboard",
                "leaf": None,
                "error": r_dash["error"],
                "message": r_dash.get("message", ""),
            }
        )
        return pages, failures

    dashboard_view = r_dash["view"]
    # Collect eids visible on the initial dashboard view so we can filter
    # them out when looking for newly-revealed leaf links after expanding
    # a top-level item.
    initial_eids: set[str] = {el["eid"] for el in dashboard_view.get("elements", [])}

    # --- Step 4: walk each top-level item -----------------------------------
    for top in top_level_items:
        if time.monotonic() - walk_start > _MAX_WALK_SECONDS:
            print(f"   [walk] time limit reached after {_MAX_WALK_SECONDS}s — stopping")
            break

        print(f"\n--- Top-level: {top}")

        # 4a. Click the top-level item.
        intent: dict[str, Any] = {
            "role": "link",
            "name": top,
            "action": "click",
            "value": None,
        }
        r_top = act_by_intent_fn(session_id, intent, action_id)
        if not r_top.get("ok"):
            err = r_top.get("failure_reason") or r_top.get("error", "unknown")
            msg = r_top.get("message", "")
            print(f"    FAIL clicking '{top}': {err}")
            failures.append({"top": top, "leaf": None, "error": err, "message": msg})
            # Reset to dashboard before next top-level.
            open_fn("/webui/#/dashboard", action_id)
            time.sleep(settle_s)
            continue

        # 4c. Wait for Angular to settle.
        time.sleep(settle_s)

        # 4d. Re-describe to get the expanded view.
        r_expanded = describe_fn(session_id)
        if "error" in r_expanded:
            failures.append(
                {
                    "top": top,
                    "leaf": None,
                    "error": r_expanded["error"],
                    "message": r_expanded.get("message", ""),
                }
            )
            open_fn("/webui/#/dashboard", action_id)
            time.sleep(settle_s)
            continue

        expanded_view = r_expanded["view"]

        # 4e. Find candidate leaf links.
        top_lower = {t.lower() for t in top_level_items}
        leaf_candidates = [
            el
            for el in expanded_view.get("elements", [])
            if (
                el.get("role") == "link"
                and el.get("eid") not in initial_eids
                and (el.get("name") or "").lower() not in top_lower
                and _is_safe_to_click(el.get("name"))
            )
        ]

        print(f"    {len(leaf_candidates)} leaf candidate(s) found")

        leaves_visited = 0
        for leaf_el in leaf_candidates[:_MAX_LEAVES_PER_TOP]:
            if time.monotonic() - walk_start > _MAX_WALK_SECONDS:
                print("   [walk] time limit reached — stopping mid-top-level")
                break

            leaf_name = leaf_el.get("name", "")
            print(f"    leaf: {leaf_name!r}")

            # 4f. Click the leaf.
            leaf_intent: dict[str, Any] = {
                "role": "link",
                "name": leaf_name,
                "action": "click",
                "value": None,
            }
            r_leaf = act_by_intent_fn(session_id, leaf_intent, action_id)
            if not r_leaf.get("ok"):
                err = r_leaf.get("failure_reason") or r_leaf.get("error", "unknown")
                msg = r_leaf.get("message", "")
                print(f"      FAIL: {err}")
                failures.append({"top": top, "leaf": leaf_name, "error": err, "message": msg})
                # Re-open dashboard and re-expand parent to keep walking.
                open_fn("/webui/#/dashboard", action_id)
                time.sleep(settle_s)
                re_click = act_by_intent_fn(session_id, intent, action_id)
                if re_click.get("ok"):
                    time.sleep(settle_s)
                continue

            time.sleep(settle_s)

            # Re-describe to capture the leaf page.
            r_page = describe_fn(session_id)
            if "error" in r_page:
                failures.append(
                    {
                        "top": top,
                        "leaf": leaf_name,
                        "error": r_page["error"],
                        "message": r_page.get("message", ""),
                    }
                )
                open_fn("/webui/#/dashboard", action_id)
                time.sleep(settle_s)
                re_click = act_by_intent_fn(session_id, intent, action_id)
                if re_click.get("ok"):
                    time.sleep(settle_s)
                continue

            page_view = r_page["view"]
            page_url = page_view.get("url", "")

            # Dedupe by URL.
            if page_url and page_url in visited_urls:
                print(f"      skip (duplicate URL: {page_url})")
            else:
                if page_url:
                    visited_urls.add(page_url)
                entry: dict[str, Any] = {
                    "breadcrumb": f"{top} > {leaf_name}",
                    "url": page_url,
                    "title": page_view.get("title", ""),
                    "discovered_via": "sidebar",
                    "elements": page_view.get("elements", []),
                    "modals": page_view.get("modals", []),
                    "errors": page_view.get("errors", []),
                }
                pages.append(entry)
                n_el = len(entry["elements"])
                print(f"      ok — {n_el} elements  url={page_url}")
                leaves_visited += 1

            # Navigate back to dashboard and re-expand the parent so the
            # remaining leaves under this top-level are reachable.
            open_fn("/webui/#/dashboard", action_id)
            time.sleep(settle_s)
            re_click = act_by_intent_fn(session_id, intent, action_id)
            if re_click.get("ok"):
                time.sleep(settle_s)

        print(f"    => {leaves_visited} page(s) recorded under '{top}'")

        # Reset to dashboard before next top-level.
        open_fn("/webui/#/dashboard", action_id)
        time.sleep(settle_s)

    return pages, failures


# ----- Markdown renderer ----------------------------------------------------


def _render_markdown(catalog: dict[str, Any]) -> str:
    """Render the catalog as a human-readable markdown report."""
    lines: list[str] = []
    lines.append(f"# Cisco WebUI element catalog — {catalog['catalog_timestamp']}")
    lines.append("")
    lines.append(f"- Router: `{catalog['router_host']}`")
    lines.append(f"- Code commit: `{catalog['git_commit']}`")
    s = catalog["summary"]
    lines.append(
        f"- Top-levels walked: {s['top_levels_walked']} "
        f"(leaves attempted: {s['leaves_attempted']}, "
        f"ok: {s['leaves_ok']}, failed: {s['leaves_failed']})"
    )
    lines.append(f"- Total elements captured: {s['total_elements']}")
    lines.append("")

    for entry in catalog.get("pages", []):
        breadcrumb = entry.get("breadcrumb", entry.get("url", "unknown"))
        url = entry.get("url", "")
        elements = entry.get("elements", [])
        modals = entry.get("modals", [])
        errors = entry.get("errors", [])
        lines.append(
            f"## {breadcrumb} "
            f"({len(elements)} elements, {len(modals)} modals, {len(errors)} errors)"
        )
        lines.append("")
        lines.append(f"- `url`: `{url}`")
        lines.append(f"- `title`: {entry.get('title', '')!r}")
        lines.append(f"- `discovered_via`: {entry.get('discovered_via', '')!r}")
        lines.append("")
        if elements:
            lines.append("| eid | role | name | value | required |")
            lines.append("| --- | --- | --- | --- | --- |")
            for el in elements:
                name = (el.get("name") or "").replace("|", "\\|")
                value = (el.get("value") or "").replace("|", "\\|") if "value" in el else ""
                required = "yes" if el.get("required") else ""
                lines.append(
                    f"| `{el['eid']}` | `{el['role']}` | {name!r} | {value!r} | {required} |"
                )
            lines.append("")
        if modals:
            lines.append("**Modals:**")
            for m in modals:
                lines.append(f"- `{m['eid']}` `{m['role']}` — {m.get('name', '')!r}")
            lines.append("")
        if errors:
            lines.append("**Errors / alerts:**")
            for e in errors:
                lines.append(f"- {e.get('name', '')!r}")
            lines.append("")

    if catalog.get("failures"):
        lines.append("## Failures")
        lines.append("")
        for f in catalog["failures"]:
            leaf_part = f" > {f['leaf']}" if f.get("leaf") else ""
            lines.append(f"- `{f['top']}{leaf_part}` — `{f['error']}`: {f.get('message', '')}")
        lines.append("")

    return "\n".join(lines)


# ----- Main -----------------------------------------------------------------


def main() -> int:
    settings = get_settings()

    # Approve a single action_id we'll reuse for every sidebar click.
    action_id = propose_action(
        tool="catalog_webui_elements",
        params={"mode": "sidebar_walk", "top_levels": TOP_LEVEL_ITEMS},
    )
    approve_action(action_id)
    print(f"=> action_id: {action_id} (APPROVED)")
    print(f"=> sidebar walk against {settings.router_host}\n")

    catalog: dict[str, Any] = {
        "catalog_timestamp": datetime.now(UTC).isoformat(),
        "router_host": settings.router_host,
        "git_commit": _git_short_sha(),
        "pages": [],
        "failures": [],
        "summary": {
            "top_levels_walked": len(TOP_LEVEL_ITEMS),
            "leaves_attempted": 0,
            "leaves_ok": 0,
            "leaves_failed": 0,
            "total_elements": 0,
        },
    }

    session_id: str | None = None
    try:
        # Open the dashboard to start the session.
        r = webui_open("/webui/#/dashboard", action_id=action_id, headless=HEADLESS)
        if "error" in r:
            print(f"FAIL opening dashboard: {r['error']} — {r.get('message', '')}")
            return 1

        session_id = r["session_id"]
        print(f"=> session_id: {session_id}")

        # Sleep so Angular finishes rendering.
        time.sleep(ANGULAR_SETTLE_S)

        # Run the sidebar walker.
        pages, failures = walk_sidebar(
            open_fn=lambda path, aid: webui_open(path, action_id=aid, headless=HEADLESS),
            describe_fn=webui_describe_page,
            act_by_intent_fn=webui_act_by_intent,
            session_id=session_id,
            action_id=action_id,
            settle_s=ANGULAR_SETTLE_S,
            top_level_items=TOP_LEVEL_ITEMS,
        )

        catalog["pages"] = pages
        catalog["failures"] = failures

        # Build summary.
        leaves_ok = len(pages)
        leaves_failed = len([f for f in failures if f.get("leaf") is not None])
        leaves_attempted = leaves_ok + leaves_failed
        total_elements = sum(len(p.get("elements", [])) for p in pages)
        catalog["summary"] = {
            "top_levels_walked": len(TOP_LEVEL_ITEMS),
            "leaves_attempted": leaves_attempted,
            "leaves_ok": leaves_ok,
            "leaves_failed": leaves_failed,
            "total_elements": total_elements,
        }

        # Write outputs.
        out_dir = Path(settings.artifacts_dir) / "webui-catalog"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        json_path = out_dir / f"catalog-{ts}.json"
        md_path = out_dir / f"catalog-{ts}.md"

        catalog_json = json.dumps(catalog, indent=2, default=str)
        json_path.write_text(catalog_json, encoding="utf-8")
        md_path.write_text(_render_markdown(catalog), encoding="utf-8")

        # Also write the blessed snapshot the backend reads in Phase 5.
        kb_dir = Path(settings.knowledge_base_dir) / "webui-catalog"
        kb_dir.mkdir(parents=True, exist_ok=True)
        blessed_path = kb_dir / "current.json"
        blessed_path.write_text(catalog_json, encoding="utf-8")

        s = catalog["summary"]
        print("\n=> Catalog complete.")
        print(
            f"   {s['leaves_ok']}/{s['leaves_attempted']} leaves succeeded, "
            f"{s['total_elements']} elements captured"
        )
        print(f"   JSON:    {json_path}")
        print(f"   MD:      {md_path}")
        print(f"   Blessed: {blessed_path}")
        return 0

    finally:
        close_all_sessions()


if __name__ == "__main__":
    sys.exit(main())
