#!/usr/bin/env python
"""Catalog the Cisco WebUI for AI grounding + Playwright reference.

Walks a list of hash routes against the real C1111, runs
`describe_page` on each via the Phase 4 slice 2 generic driver, and
saves the aggregated elements + a markdown summary to
`artifacts/webui-catalog/`.

Read-only against the router. No fills, no clicks, no Apply, no config
write. The catalog is a one-shot reconnaissance dump — Filip extends
the route list after seeing what works.

USAGE
    .venv\\Scripts\\python.exe scripts\\catalog_webui_elements.py

OUTPUT
    artifacts/webui-catalog/catalog-<UTC-timestamp>.json    machine-readable
    artifacts/webui-catalog/catalog-<UTC-timestamp>.md      human-readable

Requires ROUTER_HOST + ROUTER_WEBUI_USER + ROUTER_WEBUI_PASSWORD in
your .env. Headed Chromium opens so you can watch.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
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
    webui_describe_page,
    webui_open,
)

# ----- Configuration --------------------------------------------------------

# Set True for CI / headless re-runs. Default False so Filip can watch.
HEADLESS = False

# Seconds to wait after navigation before re-describing. Angular needs
# a moment to render after a hash-route change; the first describe (from
# webui_open) sometimes fires before the new page is fully painted, so we
# call webui_describe_page again after this sleep for a clean snapshot.
ANGULAR_SETTLE_S = 2.0

# Routes to walk. Extensible — add more after seeing which ones exist on
# your IOS XE build (the catalog will record `navigation_ok=false` for
# routes that 404 / redirect).
WEBUI_ROUTES: list[str] = [
    # Known (already used by code):
    "/webui/#/general",
    "/webui/#/vlan",
    # Likely (top-level sidebar items from selectors/iosxe_default.yaml):
    "/webui/#/dashboard",
    "/webui/#/monitoring",
    "/webui/#/configuration",
    "/webui/#/administration",
    "/webui/#/troubleshooting",
    # Plausible IOS XE 17.x sub-pages:
    "/webui/#/interfaces",
    "/webui/#/routing",
    "/webui/#/dhcp",
    "/webui/#/users",
    "/webui/#/dayZeroRouting",
]

# ----- Helpers --------------------------------------------------------------


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


def _render_markdown(catalog: dict[str, Any]) -> str:
    """Render the catalog as a human-readable markdown report."""
    lines: list[str] = []
    lines.append(f"# Cisco WebUI element catalog — {catalog['catalog_timestamp']}")
    lines.append("")
    lines.append(f"- Router: `{catalog['router_host']}`")
    lines.append(f"- Code commit: `{catalog['git_commit']}`")
    s = catalog["summary"]
    lines.append(
        f"- Routes walked: {s['total_routes']} (ok: {s['ok_count']}, failed: {s['failed_count']})"
    )
    lines.append(f"- Total elements captured: {s['total_elements']}")
    lines.append("")

    for entry in catalog["routes"]:
        path = entry["path"]
        if not entry.get("navigation_ok"):
            lines.append(f"## `{path}` — FAILED")
            lines.append("")
            lines.append(f"- Error: `{entry.get('error')}`")
            lines.append(f"- Message: `{entry.get('message')}`")
            lines.append("")
            continue

        view = entry["view"]
        lines.append(
            f"## `{path}` — {view.get('title', '')} "
            f"({len(view['elements'])} elements, "
            f"{len(view['modals'])} modals, "
            f"{len(view['errors'])} errors)"
        )
        lines.append("")
        lines.append(f"- `view_id`: `{view['view_id']}`")
        lines.append(f"- `url`: `{view['url']}`")
        lines.append("")
        if view["elements"]:
            lines.append("| eid | role | name | value | required |")
            lines.append("| --- | --- | --- | --- | --- |")
            for el in view["elements"]:
                name = (el.get("name") or "").replace("|", "\\|")
                value = (el.get("value") or "").replace("|", "\\|") if "value" in el else ""
                required = "yes" if el.get("required") else ""
                lines.append(
                    f"| `{el['eid']}` | `{el['role']}` | {name!r} | {value!r} | {required} |"
                )
            lines.append("")
        if view["modals"]:
            lines.append("**Modals:**")
            for m in view["modals"]:
                lines.append(f"- `{m['eid']}` `{m['role']}` — {m.get('name', '')!r}")
            lines.append("")
        if view["errors"]:
            lines.append("**Errors / alerts:**")
            for e in view["errors"]:
                lines.append(f"- {e.get('name', '')!r}")
            lines.append("")

    return "\n".join(lines)


# ----- Main -----------------------------------------------------------------


def main() -> int:
    settings = get_settings()

    # Approve a single action_id we'll reuse for every route. The catalog
    # calls only read-only tools, but we still create one for symmetry and
    # so the pre-snapshot logic fires once at session start.
    action_id = propose_action(
        tool="catalog_webui_elements",
        params={"routes": WEBUI_ROUTES},
    )
    approve_action(action_id)
    print(f"=> action_id: {action_id} (APPROVED)")
    print(f"=> walking {len(WEBUI_ROUTES)} routes against {settings.router_host}\n")

    catalog: dict[str, Any] = {
        "catalog_timestamp": datetime.now(UTC).isoformat(),
        "router_host": settings.router_host,
        "git_commit": _git_short_sha(),
        "routes": [],
        "summary": {
            "total_routes": len(WEBUI_ROUTES),
            "ok_count": 0,
            "failed_count": 0,
            "total_elements": 0,
        },
    }

    session_id: str | None = None
    try:
        for route in WEBUI_ROUTES:
            print(f"--- {route}")

            # webui_open both navigates AND describes. For first call it
            # creates the session; subsequent calls with the same action_id
            # reuse it (just navigate to the new route).
            r = webui_open(route, action_id=action_id, headless=HEADLESS)
            if "error" in r:
                print(f"    FAIL: {r['error']} — {r.get('message', '')}")
                catalog["routes"].append(
                    {
                        "path": route,
                        "navigation_ok": False,
                        "error": r["error"],
                        "message": r.get("message"),
                    }
                )
                catalog["summary"]["failed_count"] += 1
                continue

            if session_id is None:
                session_id = r["session_id"]

            # Sleep so Angular finishes rendering the new hash route.
            time.sleep(ANGULAR_SETTLE_S)

            # Re-describe to get the post-render view.
            r2 = webui_describe_page(session_id)
            if "error" in r2:
                # Fall back to the first describe from webui_open.
                view = r["view"]
                print(f"    re-describe failed ({r2['error']}) — using webui_open view")
            else:
                view = r2["view"]

            n_el = len(view["elements"])
            print(
                f"    ok: {n_el} elements, "
                f"{len(view['modals'])} modals, "
                f"{len(view['errors'])} errors  (view_id={view['view_id']})"
            )
            catalog["routes"].append({"path": route, "navigation_ok": True, "view": view})
            catalog["summary"]["ok_count"] += 1
            catalog["summary"]["total_elements"] += n_el

        # Write outputs.
        out_dir = Path(settings.artifacts_dir) / "webui-catalog"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        json_path = out_dir / f"catalog-{ts}.json"
        md_path = out_dir / f"catalog-{ts}.md"

        json_path.write_text(json.dumps(catalog, indent=2, default=str), encoding="utf-8")
        md_path.write_text(_render_markdown(catalog), encoding="utf-8")

        s = catalog["summary"]
        print("\n=> Catalog complete.")
        print(
            f"   {s['ok_count']}/{s['total_routes']} routes succeeded, "
            f"{s['total_elements']} elements captured"
        )
        print(f"   JSON: {json_path}")
        print(f"   MD:   {md_path}")
        return 0

    finally:
        close_all_sessions()


if __name__ == "__main__":
    sys.exit(main())
