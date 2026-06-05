"""Unit tests for backend/webui_agent/perceive.py.

Uses MagicMock pages + a real AtlasStore backed by a tmp_path directory.
No real browser required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.webui_agent.atlas.schema import (
    ControlSpec,
    FieldSpec,
    LocatorSpec,
    RouteAtlas,
    SuccessSignal,
)
from backend.webui_agent.atlas.store import AtlasStore
from backend.webui_agent.perceive import (
    PerceiveResult,
    get_live_url,
    perceive_page,
    route_from_url,
)

pytestmark = pytest.mark.webui

# ---------------------------------------------------------------------------
# Test fingerprint constant
# ---------------------------------------------------------------------------

_FP = "c1111-4p__17-6-3a"

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> AtlasStore:
    return AtlasStore(tmp_path / "atlas", _FP)


def _make_subnet_mask_atlas(route: str = "#/ospf") -> RouteAtlas:
    """Atlas with a 'Subnet Mask' combobox field and an Apply button."""
    return RouteAtlas(
        route=route,
        device_fingerprint=_FP,
        page_title="OSPF",
        fields=[
            FieldSpec(
                key="subnet_mask",
                label="Subnet Mask",
                role="combobox",
                widget="kendo_combobox",
                required=False,
                locator=LocatorSpec(strategy="get_by_role", role="combobox", name="Subnet Mask"),
                options=["255.255.255.0", "255.255.255.128"],
                kendo_select_name="subnetmaskOptions",
            ),
        ],
        apply_controls=[
            ControlSpec(
                key="apply-to-device",
                label="Apply to Device",
                role="button",
                is_router_write=True,
            )
        ],
        success_signal=SuccessSignal("a11y_text", "success"),
    )


def _make_mock_page(
    *,
    url: str = "https://r/webui/#/ospf",
    accessibility_snapshot: dict | None = None,
    evaluate_side_effects=None,  # list of return values for repeated evaluate calls
) -> MagicMock:
    """Return a MagicMock page with accessibility.snapshot stubbed."""
    page = MagicMock()
    # window.location.href evaluate
    if evaluate_side_effects is not None:
        page.evaluate.side_effect = evaluate_side_effects
    else:
        page.evaluate.return_value = url
    # accessibility.snapshot
    snap = accessibility_snapshot or {
        "role": "WebArea",
        "name": "",
        "children": [
            {"role": "listbox", "name": "Subnet Mask", "value": "255.255.255.0"},
        ],
    }
    page.accessibility.snapshot.return_value = snap
    page.title.return_value = "OSPF"
    return page


# ---------------------------------------------------------------------------
# route_from_url
# ---------------------------------------------------------------------------


def test_route_from_url_hash_fragment():
    assert route_from_url("https://r/webui/#/ospf") == "#/ospf"


def test_route_from_url_hash_fragment_dhcp():
    assert route_from_url("https://r/webui/#/dhcp") == "#/dhcp"


def test_route_from_url_no_hash():
    url = "https://r/webui/general"
    result = route_from_url(url)
    assert result == url  # no hash → return full url


def test_route_from_url_empty():
    assert route_from_url("") == ""


def test_route_from_url_only_hash():
    # "https://r/#" → fragment is "" → returns url
    result = route_from_url("https://r/#")
    # empty fragment after "#" → should return url unchanged
    assert result == "https://r/#"


# ---------------------------------------------------------------------------
# get_live_url
# ---------------------------------------------------------------------------


def test_get_live_url_returns_evaluate_result():
    page = MagicMock()
    page.evaluate.return_value = "https://r/webui/#/ospf"
    assert get_live_url(page) == "https://r/webui/#/ospf"


def test_get_live_url_handles_exception():
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("page closed")
    assert get_live_url(page) == ""


# ---------------------------------------------------------------------------
# THE KEY TEST: perceive names kendo field by atlas label, not live value
# ---------------------------------------------------------------------------


def test_perceive_names_kendo_field_by_label_not_value(tmp_path: Path):
    """The view must use the atlas label 'Subnet Mask', not the live value '255.255.255.0'."""
    store = _make_store(tmp_path)
    atlas = _make_subnet_mask_atlas()
    store.save_route(atlas)

    page = _make_mock_page(
        url="https://r/webui/#/ospf",
        accessibility_snapshot={
            "role": "WebArea",
            "name": "",
            "children": [
                # Live node: role=listbox, name=Subnet Mask (matched), value=255.255.255.0
                {"role": "listbox", "name": "Subnet Mask", "value": "255.255.255.0"},
            ],
        },
    )

    result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert isinstance(result, PerceiveResult)
    fields = result.view.get("fields", [])
    assert len(fields) == 1
    field = fields[0]
    assert field["label"] == "Subnet Mask", (
        f"label must be atlas label, got {field['label']!r}"
    )
    assert field["value"] == "255.255.255.0"
    assert field["role"] in ("combobox", "listbox")


# ---------------------------------------------------------------------------
# test_perceive_captures_on_miss
# ---------------------------------------------------------------------------


def test_perceive_captures_on_miss(tmp_path: Path):
    """Empty store → perceive calls capture_route, saves the atlas, captured=True."""
    store = _make_store(tmp_path)

    # Canned descriptors for capture_route (via extract_descriptors → page.evaluate).
    canned_descriptors = [
        {
            "tag": "span",
            "type": "",
            "role": "listbox",
            "classes": "",
            "aria_label": "Subnet Mask",
            "labelledby_text": "",
            "label_for_text": "",
            "spatial_label": "",
            "placeholder": "",
            "title": "",
            "name_attr": "",
            "id": "",
            "ng_model": "",
            "kendo_select_name": "subnetmaskOptions",
            "options": ["255.255.255.0"],
            "aria_controls": "",
            "required": False,
            "checked": None,
            "is_kendo_numeric": False,
            "is_kendo_grid": False,
            "bbox": {"x": 100, "y": 100, "w": 150, "h": 30},
        }
    ]

    # page.evaluate is called twice:
    #   1. get_live_url → returns url string
    #   2. capture_route → extract_descriptors → returns descriptors list
    page = _make_mock_page(
        url="https://r/webui/#/ospf",
        evaluate_side_effects=[
            "https://r/webui/#/ospf",  # get_live_url
            canned_descriptors,        # extract_descriptors
        ],
        accessibility_snapshot={
            "role": "WebArea",
            "children": [
                {"role": "listbox", "name": "Subnet Mask", "value": "255.255.255.0"},
            ],
        },
    )

    result = perceive_page(page, store, device_fingerprint=_FP)

    assert result.captured is True

    # Atlas file must exist on disk.
    from backend.webui_agent.atlas.fingerprint import route_slug
    atlas_path = tmp_path / "atlas" / _FP / "routes" / f"{route_slug('#/ospf')}.json"
    assert atlas_path.exists(), f"atlas file not found at {atlas_path}"


# ---------------------------------------------------------------------------
# test_perceive_calls_accessibility_snapshot_exactly_once (no-drift path)
# ---------------------------------------------------------------------------


def test_perceive_calls_accessibility_snapshot_exactly_once(tmp_path: Path):
    """On the no-drift path accessibility.snapshot must be called exactly once."""
    store = _make_store(tmp_path)
    atlas = _make_subnet_mask_atlas()
    store.save_route(atlas)

    page = _make_mock_page(
        url="https://r/webui/#/ospf",
        accessibility_snapshot={
            "role": "WebArea",
            "children": [
                {"role": "listbox", "name": "Subnet Mask", "value": "255.255.255.0"},
            ],
        },
    )

    perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert page.accessibility.snapshot.call_count == 1


# ---------------------------------------------------------------------------
# test_perceive_drift_triggers_one_recapture
# ---------------------------------------------------------------------------


def test_perceive_drift_triggers_one_recapture(tmp_path: Path):
    """When drift is detected, capture_route is called again and snapshot called twice."""
    store = _make_store(tmp_path)

    # Atlas with a REQUIRED field "Process ID" that is missing from the live snapshot.
    atlas = RouteAtlas(
        route="#/ospf",
        device_fingerprint=_FP,
        page_title="OSPF",
        fields=[
            FieldSpec(
                key="process_id",
                label="Process ID",
                role="textbox",
                widget="input",
                required=True,
                locator=LocatorSpec(strategy="get_by_role", role="textbox", name="Process ID"),
            ),
        ],
        success_signal=SuccessSignal("a11y_text", "success"),
    )
    store.save_route(atlas)

    # Canned descriptors for recapture — now Process ID IS present in DOM.
    recapture_descriptors = [
        {
            "tag": "input",
            "type": "text",
            "role": "",
            "classes": "",
            "aria_label": "Process ID",
            "labelledby_text": "",
            "label_for_text": "",
            "spatial_label": "",
            "placeholder": "",
            "title": "",
            "name_attr": "processId",
            "id": "",
            "ng_model": "",
            "kendo_select_name": None,
            "options": [],
            "aria_controls": "",
            "required": True,
            "checked": None,
            "is_kendo_numeric": False,
            "is_kendo_grid": False,
            "bbox": {"x": 100, "y": 100, "w": 150, "h": 30},
        }
    ]

    # Snapshots:
    #   1st snapshot: Process ID is ABSENT (drift)
    #   2nd snapshot: Process ID IS present (after recapture)
    snap1 = {"role": "WebArea", "children": [{"role": "button", "name": "Cancel"}]}
    snap2 = {"role": "WebArea", "children": [
        {"role": "textbox", "name": "Process ID", "value": "1"},
    ]}

    # evaluate calls:
    #   1. get_live_url
    #   2. capture_route → extract_descriptors (triggered by drift)
    page = MagicMock()
    page.evaluate.side_effect = [
        "https://r/webui/#/ospf",  # get_live_url
        recapture_descriptors,     # extract_descriptors on recapture
    ]
    page.accessibility.snapshot.side_effect = [snap1, snap2]
    page.title.return_value = "OSPF"

    perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    # snapshot must be called twice (initial + re-verify after recapture).
    assert page.accessibility.snapshot.call_count == 2, (
        f"expected 2 snapshot calls, got {page.accessibility.snapshot.call_count}"
    )

    # No infinite loop guard: evaluate called at most twice.
    assert page.evaluate.call_count <= 2


# ---------------------------------------------------------------------------
# Test that drift loop never runs more than once
# ---------------------------------------------------------------------------


def test_perceive_no_infinite_loop_on_persistent_drift(tmp_path: Path):
    """Even if drift persists after recapture, snapshot is still called only twice."""
    store = _make_store(tmp_path)

    atlas = RouteAtlas(
        route="#/ospf",
        device_fingerprint=_FP,
        page_title="OSPF",
        fields=[
            FieldSpec(
                key="req_field",
                label="Required Field",
                role="textbox",
                widget="input",
                required=True,
                locator=LocatorSpec(
                    strategy="get_by_role", role="textbox", name="Required Field"
                ),
            ),
        ],
        success_signal=SuccessSignal("a11y_text", "success"),
    )
    store.save_route(atlas)

    # Both snapshots return empty children (drift persists).
    empty_snap = {"role": "WebArea", "children": [{"role": "button", "name": "Cancel"}]}

    recapture_descriptors: list[dict] = []  # empty → captured atlas also has no fields

    page = MagicMock()
    page.evaluate.side_effect = [
        "https://r/webui/#/ospf",
        recapture_descriptors,
    ]
    page.accessibility.snapshot.return_value = empty_snap
    page.title.return_value = "OSPF"

    result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    # Must never loop: snapshot called at most twice.
    assert page.accessibility.snapshot.call_count <= 2
    # Result is still a PerceiveResult.
    assert isinstance(result, PerceiveResult)


# ---------------------------------------------------------------------------
# Test captured=True on miss (store empty)
# ---------------------------------------------------------------------------


def test_perceive_captured_false_when_atlas_exists(tmp_path: Path):
    """When atlas already exists in the store, captured must be False."""
    store = _make_store(tmp_path)
    atlas = _make_subnet_mask_atlas()
    store.save_route(atlas)

    page = _make_mock_page(
        url="https://r/webui/#/ospf",
        accessibility_snapshot={
            "role": "WebArea",
            "children": [
                {"role": "listbox", "name": "Subnet Mask", "value": "255.255.255.0"},
            ],
        },
    )

    result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")
    assert result.captured is False
