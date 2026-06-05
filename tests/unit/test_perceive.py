"""Unit tests for backend/webui_agent/perceive.py.

C5 rewrite: perceive_page now uses DOM extraction (capture.extract_descriptors)
directly — NO accessibility.snapshot calls in the hot path.  All tests here
mock capture.extract_descriptors and assert the DOM-keyed view.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.webui_agent.atlas.schema import (
    RouteAtlas,
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
# Canned descriptor builders
# ---------------------------------------------------------------------------


def _canned_subnet_mask_desc(value: str = "255.255.255.0") -> dict:
    """A Kendo combobox descriptor for 'Subnet Mask' — has stable identity via kendo_select_name."""
    return {
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
        "options": ["255.255.255.0", "255.255.255.128"],
        "aria_controls": "",
        "required": False,
        "checked": None,
        "is_kendo_numeric": False,
        "is_kendo_grid": False,
        "bbox": {"x": 100, "y": 100, "w": 150, "h": 30},
        "inner_text": "",
        "value": value,
    }


def _canned_process_id_desc(value: str = "") -> dict:
    """A plain text input for Process ID — stable identity via name_attr."""
    return {
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
        "bbox": {"x": 100, "y": 160, "w": 150, "h": 30},
        "inner_text": "",
        "value": value,
    }


def _canned_add_button_desc() -> dict:
    """An 'Add' button with inner_text='Add' (no aria_label, no name_attr)."""
    return {
        "tag": "button",
        "type": "",
        "role": "button",
        "classes": "",
        "aria_label": "",
        "labelledby_text": "",
        "label_for_text": "",
        "spatial_label": "",
        "placeholder": "",
        "title": "",
        "name_attr": "",
        "id": "",
        "ng_model": "",
        "kendo_select_name": None,
        "options": [],
        "aria_controls": "",
        "required": False,
        "checked": None,
        "is_kendo_numeric": False,
        "is_kendo_grid": False,
        "bbox": {"x": 100, "y": 50, "w": 60, "h": 30},
        "inner_text": "Add",
        "value": "",
    }


def _canned_apply_button_desc() -> dict:
    """An 'Apply to Device' button with primaryActionButton class."""
    return {
        "tag": "button",
        "type": "",
        "role": "button",
        "classes": "btn btn-primary primaryActionButton",
        "aria_label": "Apply to Device",
        "labelledby_text": "",
        "label_for_text": "",
        "spatial_label": "",
        "placeholder": "",
        "title": "",
        "name_attr": "",
        "id": "",
        "ng_model": "",
        "kendo_select_name": None,
        "options": [],
        "aria_controls": "",
        "required": False,
        "checked": None,
        "is_kendo_numeric": False,
        "is_kendo_grid": False,
        "bbox": {"x": 200, "y": 300, "w": 120, "h": 30},
        "inner_text": "Apply to Device",
        "value": "",
    }


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> AtlasStore:
    return AtlasStore(tmp_path / "atlas", _FP)


def _make_mock_page(url: str = "https://r/webui/#/ospf") -> MagicMock:
    """Return a MagicMock page with evaluate returning the URL and title stubbed."""
    page = MagicMock()
    page.evaluate.return_value = url
    page.title.return_value = "OSPF"
    # Ensure accessibility.snapshot is present (for any code that might call it)
    # but we ASSERT it is never called in perceive_page.
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
# perceive_page — DOM-extraction path (no accessibility.snapshot)
# ---------------------------------------------------------------------------


def test_perceive_no_accessibility_snapshot_call(tmp_path: Path):
    """perceive_page must NEVER call page.accessibility.snapshot."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_subnet_mask_desc("255.255.255.0")]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    # accessibility.snapshot must NOT have been called.
    page.accessibility.snapshot.assert_not_called()


def test_perceive_field_keyed_by_dom_name(tmp_path: Path):
    """Fields are keyed by DOM name_attr / kendo_select_name, not a11y node names."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [
        _canned_subnet_mask_desc("255.255.255.128"),
        _canned_process_id_desc("1"),
    ]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert isinstance(result, PerceiveResult)
    fields = result.view.get("fields", [])
    keys = [f["key"] for f in fields]
    # Subnet Mask keyed by kendo_select_name ("subnetmaskoptions")
    assert any("subnet" in k or "mask" in k for k in keys), f"no subnet key in {keys}"
    # Process ID keyed by name_attr processId → processid
    assert "processid" in keys, f"processid not in {keys}"


def test_perceive_field_carries_dom_value(tmp_path: Path):
    """Field values come from the descriptor's 'value' key (live DOM state)."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_process_id_desc(value="42")]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    fields = result.view.get("fields", [])
    process_field = next((f for f in fields if f["key"] == "processid"), None)
    assert process_field is not None
    assert process_field["value"] == "42"


def test_perceive_subnet_mask_field_labeled_correctly(tmp_path: Path):
    """Subnet Mask combobox field has correct label and options in view."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_subnet_mask_desc("255.255.255.0")]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    fields = result.view.get("fields", [])
    subnet_field = next((f for f in fields if "subnet" in f["key"] or "mask" in f["key"]), None)
    assert subnet_field is not None, f"no subnet field in {[f['key'] for f in fields]}"
    assert subnet_field["label"] == "Subnet Mask"
    assert subnet_field["options"] == ["255.255.255.0", "255.255.255.128"]
    assert subnet_field["value"] == "255.255.255.0"


def test_perceive_saves_atlas_to_store(tmp_path: Path):
    """perceive_page must save the atlas to the store."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_process_id_desc()]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    from backend.webui_agent.atlas.fingerprint import route_slug
    atlas_path = tmp_path / "atlas" / _FP / "routes" / f"{route_slug('#/ospf')}.json"
    assert atlas_path.exists(), f"atlas file not found at {atlas_path}"


def test_perceive_returns_perceive_result(tmp_path: Path):
    """perceive_page returns a PerceiveResult with expected structure."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_subnet_mask_desc()]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert isinstance(result, PerceiveResult)
    assert result.view["route"] == "#/ospf"
    assert "fields" in result.view
    assert "apply_controls" in result.view
    assert "open_form_control" in result.view
    assert isinstance(result.atlas, RouteAtlas)


def test_perceive_empty_extract_triggers_retry(tmp_path: Path):
    """When extract_descriptors returns empty/no real controls, retry fires once."""
    store = _make_store(tmp_path)
    page = _make_mock_page()

    call_count: list[int] = [0]
    real_descriptors = [_canned_process_id_desc("1")]

    def _extract(p: object) -> list[dict]:
        call_count[0] += 1
        if call_count[0] == 1:
            return []  # first call: no controls (Angular not rendered yet)
        return real_descriptors  # second call: real field present

    with patch("backend.webui_agent.perceive.extract_descriptors", side_effect=_extract):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    # Retry must have fired: extract_descriptors called exactly twice.
    assert call_count[0] == 2, f"expected 2 extract calls, got {call_count[0]}"
    # The result fields come from the retry.
    fields = result.view.get("fields", [])
    assert any(f["key"] == "processid" for f in fields)


def test_perceive_missing_required_detected(tmp_path: Path):
    """Required fields with empty value appear in missing_required."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    # Process ID is required, value is empty string → missing
    descriptors = [_canned_process_id_desc(value="")]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert "processid" in result.missing_required


def test_perceive_not_missing_when_value_present(tmp_path: Path):
    """Required fields with a non-empty value do NOT appear in missing_required."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_process_id_desc(value="1")]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert "processid" not in result.missing_required


def test_perceive_open_form_control_from_add_button(tmp_path: Path):
    """The 'Add' button (labeled by inner_text) becomes open_form_control in the view."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_add_button_desc(), _canned_process_id_desc()]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    ofc = result.view.get("open_form_control")
    assert ofc is not None, "open_form_control must not be None"
    assert ofc["label"] == "Add"


def test_perceive_apply_control_detected(tmp_path: Path):
    """The primaryActionButton 'Apply to Device' appears in apply_controls."""
    store = _make_store(tmp_path)
    page = _make_mock_page()
    descriptors = [_canned_apply_button_desc(), _canned_process_id_desc()]

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=descriptors
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    apply_controls = result.view.get("apply_controls", [])
    assert len(apply_controls) >= 1
    labels = [c["label"] for c in apply_controls]
    assert "Apply to Device" in labels


def test_perceive_drift_always_false(tmp_path: Path):
    """drift is always False in the new DOM-extraction path (no reconcile)."""
    store = _make_store(tmp_path)
    page = _make_mock_page()

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=[]
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert result.drift is False


def test_perceive_captured_always_true(tmp_path: Path):
    """captured is always True in the new DOM-extraction path."""
    store = _make_store(tmp_path)
    page = _make_mock_page()

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=[]
    ):
        result = perceive_page(page, store, device_fingerprint=_FP, route="#/ospf")

    assert result.captured is True


def test_perceive_route_from_url_when_not_supplied(tmp_path: Path):
    """When route is not supplied, it is derived from window.location.href."""
    store = _make_store(tmp_path)
    page = _make_mock_page(url="https://r/webui/#/dhcp")

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=[]
    ):
        result = perceive_page(page, store, device_fingerprint=_FP)

    assert result.view["route"] == "#/dhcp"


def test_perceive_route_unknown_when_url_empty(tmp_path: Path):
    """When URL evaluate fails, route falls back to 'unknown'."""
    store = _make_store(tmp_path)
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("page closed")
    page.title.return_value = ""

    with patch(
        "backend.webui_agent.perceive.extract_descriptors", return_value=[]
    ):
        result = perceive_page(page, store, device_fingerprint=_FP)

    assert result.view["route"] == "unknown"
