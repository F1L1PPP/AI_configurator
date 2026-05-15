"""Unit tests for scripts/catalog_webui_elements.py sidebar walker.

Covers:
- _is_safe_to_click safety helper
- walk_sidebar page-collection logic (mock-based, no real Playwright)
"""

from __future__ import annotations

# Add scripts/ dir to path so the import below resolves without the project
# being installed as a package.
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from catalog_webui_elements import _is_safe_to_click, walk_sidebar  # noqa: E402

# ---------------------------------------------------------------------------
# _is_safe_to_click — accepts normal navigation names
# ---------------------------------------------------------------------------


def test_is_safe_to_click_accepts_normal_navigation():
    safe_names = [
        "Static Routing",
        "OSPF",
        "Configuration",
        "Dashboard",
        "Monitoring",
        "Troubleshooting",
        "EIGRP",
        "ISIS",
        "VLAN",
        "Interface",
        "Administration",
    ]
    for name in safe_names:
        assert _is_safe_to_click(name) is True, f"expected safe: {name!r}"


# ---------------------------------------------------------------------------
# _is_safe_to_click — rejects destructive / form-action names
# ---------------------------------------------------------------------------


def test_is_safe_to_click_rejects_destructive():
    unsafe_names = [
        "Factory Reset",
        "factory reset",
        "FACTORY RESET",
        "Reboot",
        "Restart",
        "Delete User",
        "Remove",
        "Restore Configuration",
        "Disable HTTP Server",
        "Clear Configuration",
        "Apply",
        "Save",
        "Submit",
        "OK",
        "Confirm",
        # Case-insensitive checks
        "apply",
        "SAVE",
        "Ok",
    ]
    for name in unsafe_names:
        assert _is_safe_to_click(name) is False, f"expected unsafe: {name!r}"


# ---------------------------------------------------------------------------
# _is_safe_to_click — rejects empty / whitespace
# ---------------------------------------------------------------------------


def test_is_safe_to_click_rejects_empty():
    assert _is_safe_to_click("") is False
    assert _is_safe_to_click(None) is False  # type: ignore[arg-type]
    assert _is_safe_to_click("   ") is False
    assert _is_safe_to_click("\t") is False


# ---------------------------------------------------------------------------
# walk_sidebar — mock-based integration test for page collection logic
# ---------------------------------------------------------------------------

# Helper factories for fake view objects.


def _make_view(
    view_id: str = "v1",
    url: str = "https://192.168.10.1/webui/#/dashboard",
    title: str = "Dashboard",
    elements: list[dict[str, Any]] | None = None,
    modals: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "view_id": view_id,
        "url": url,
        "title": title,
        "elements": elements or [],
        "modals": modals or [],
        "errors": errors or [],
    }


def _link(eid: str, name: str) -> dict[str, Any]:
    return {"eid": eid, "role": "link", "name": name}


def test_walker_collects_leaves_under_top_levels():
    """walk_sidebar builds page entries with correct breadcrumbs.

    Scenario:
    - Dashboard initially has 2 top-level links (Dashboard, Configuration).
    - After clicking "Configuration", 4 new leaf links appear:
      Interface, VLAN, OSPF, Static Routing.
    - Each leaf click succeeds and returns a page view with a unique URL.

    Assertions:
    - All 4 leaves produce entries in `pages`.
    - breadcrumbs are "Configuration > <leaf_name>".
    - discovered_via == "sidebar" for all entries.
    - No failures recorded.
    - URL deduplication works: if a leaf returns a URL already seen,
      it is skipped (tested via a 5th duplicate leaf).
    """
    # --- Shared state for the mock to simulate sidebar expansion ------------

    # Top-level links visible on the initial dashboard.
    dashboard_links = [
        _link("e-dashboard", "Dashboard"),
        _link("e-config", "Configuration"),
    ]
    # Extra leaf links that appear after expanding "Configuration".
    config_leaf_links = [
        _link("e-iface", "Interface"),
        _link("e-vlan", "VLAN"),
        _link("e-ospf", "OSPF"),
        _link("e-static", "Static Routing"),
        # Duplicate — same URL as OSPF, should be deduped.
        _link("e-ospf-dup", "OSPF Duplicate"),
    ]

    # URL map — what URL each leaf click results in.
    leaf_urls: dict[str, str] = {
        "Interface": "https://192.168.10.1/webui/#/interfaces",
        "VLAN": "https://192.168.10.1/webui/#/vlan",
        "OSPF": "https://192.168.10.1/webui/#/ospf",
        "Static Routing": "https://192.168.10.1/webui/#/staticRouting",
        "OSPF Duplicate": "https://192.168.10.1/webui/#/ospf",  # duplicate URL
    }

    # Describe call counter — used to determine which view to return.
    describe_calls: list[str] = []
    current_leaf: list[str] = [""]  # mutable slot

    # --- open_fn mock -------------------------------------------------------

    def fake_open(path: str, action_id: str) -> dict[str, Any]:
        current_leaf[0] = ""
        view = _make_view(
            view_id="v-dash",
            url=f"https://192.168.10.1{path}",
            title="Dashboard",
            elements=dashboard_links,
        )
        return {"view": view, "session_id": "sess_test"}

    # --- describe_fn mock ---------------------------------------------------

    def fake_describe(session_id: str) -> dict[str, Any]:
        describe_calls.append(current_leaf[0])
        if current_leaf[0] == "Configuration":
            # After clicking Configuration, show top-level + leaves.
            elements = dashboard_links + config_leaf_links
            view = _make_view(
                view_id="v-config-expanded",
                url="https://192.168.10.1/webui/#/dashboard",
                title="Dashboard",
                elements=elements,
            )
        elif current_leaf[0] in leaf_urls:
            url = leaf_urls[current_leaf[0]]
            view = _make_view(
                view_id=f"v-{current_leaf[0]}",
                url=url,
                title=f"LAB-R4:: Cisco C1111-4P - {current_leaf[0]}",
                elements=[{"eid": "e-page-el", "role": "heading", "name": current_leaf[0]}],
            )
        else:
            # Initial dashboard describe (before any click).
            view = _make_view(
                view_id="v-dash-initial",
                url="https://192.168.10.1/webui/#/dashboard",
                title="Dashboard",
                elements=dashboard_links,
            )
        return {"view": view, "session_id": session_id}

    # --- act_by_intent_fn mock ----------------------------------------------

    def fake_act(session_id: str, intent: dict[str, Any], action_id: str) -> dict[str, Any]:
        name = intent.get("name", "")
        current_leaf[0] = name
        return {"ok": True, "chosen_eid": f"e-{name.lower().replace(' ', '-')}"}

    # --- Run the walker -----------------------------------------------------

    pages, failures = walk_sidebar(
        open_fn=fake_open,
        describe_fn=fake_describe,
        act_by_intent_fn=fake_act,
        session_id="sess_test",
        action_id="act_test",
        settle_s=0.0,  # no sleep in tests
        top_level_items=["Configuration"],  # walk only Configuration for speed
    )

    # --- Assertions ---------------------------------------------------------

    # 4 unique-URL leaves; the 5th (OSPF Duplicate) is skipped as duplicate.
    leaf_pages = [p for p in pages if p.get("discovered_via") == "sidebar"]
    assert len(leaf_pages) == 4, (
        f"expected 4 pages, got {len(leaf_pages)}: {[p['breadcrumb'] for p in pages]}"
    )

    breadcrumbs = {p["breadcrumb"] for p in leaf_pages}
    assert "Configuration > Static Routing" in breadcrumbs
    assert "Configuration > OSPF" in breadcrumbs
    assert "Configuration > VLAN" in breadcrumbs
    assert "Configuration > Interface" in breadcrumbs

    # All entries have discovered_via == "sidebar".
    for p in leaf_pages:
        assert p["discovered_via"] == "sidebar"

    # URL deduplication: OSPF Duplicate has the same URL as OSPF — should
    # not have produced a second entry.
    ospf_entries = [p for p in leaf_pages if "OSPF" in p["breadcrumb"]]
    assert len(ospf_entries) == 1, "duplicate URL should produce only 1 entry"

    # No failures for the happy-path scenario.
    assert failures == [], f"unexpected failures: {failures}"
