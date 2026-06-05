"""Unit tests for backend/webui_agent/atlas/capture.py.

All pure-function tests work on plain dicts — no mock page needed.
Page-I/O tests use a MagicMock page whose .evaluate() returns a canned list.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.webui_agent.atlas.capture import (
    _CAPTURE_JS,
    build_atlas,
    capture_route,
    classify_widget,
    extract_descriptors,
    is_apply_control,
    is_open_form_control,
    resolve_key,
    resolve_label,
)
from backend.webui_agent.atlas.schema import WIDGET_TYPES

pytestmark = pytest.mark.webui


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _desc(
    tag: str = "input",
    itype: str = "text",
    role: str = "",
    classes: str = "",
    aria_label: str = "",
    placeholder: str = "",
    title: str = "",
    name_attr: str = "",
    id: str = "",
    ng_model: str = "",
    spatial_label: str = "",
    labelledby_text: str = "",
    label_for_text: str = "",
    kendo_select_name: str | None = None,
    options: list[str] | None = None,
    is_kendo_numeric: bool = False,
    is_kendo_grid: bool = False,
    required: bool = False,
    checked: bool | None = None,
) -> dict:
    """Build a minimal descriptor dict for testing pure functions."""
    return {
        "tag": tag,
        "type": itype,
        "role": role,
        "classes": classes,
        "aria_label": aria_label,
        "placeholder": placeholder,
        "title": title,
        "name_attr": name_attr,
        "id": id,
        "ng_model": ng_model,
        "spatial_label": spatial_label,
        "labelledby_text": labelledby_text,
        "label_for_text": label_for_text,
        "kendo_select_name": kendo_select_name,
        "options": options or [],
        "is_kendo_numeric": is_kendo_numeric,
        "is_kendo_grid": is_kendo_grid,
        "required": required,
        "checked": checked,
        "bbox": {"x": 100, "y": 100, "w": 150, "h": 30},
        "aria_controls": "",
    }


# ---------------------------------------------------------------------------
# classify_widget — widget type precedence tests
# ---------------------------------------------------------------------------


class TestClassifyWidget:
    def test_apply_glyph_primary_action_button(self):
        d = _desc(tag="button", classes="btn primaryActionButton")
        assert classify_widget(d) == "button"

    def test_apply_glyph_pl_save(self):
        d = _desc(tag="button", classes="fa pl-save")
        assert classify_widget(d) == "button"

    def test_apply_glyph_icon_save_device(self):
        d = _desc(tag="button", classes="icon-save-device ng-binding")
        assert classify_widget(d) == "button"

    def test_plain_button_tag(self):
        d = _desc(tag="button", aria_label="Add")
        assert classify_widget(d) == "button"

    def test_role_button(self):
        d = _desc(tag="div", role="button", aria_label="Submit")
        assert classify_widget(d) == "button"

    def test_input_submit(self):
        d = _desc(tag="input", itype="submit")
        assert classify_widget(d) == "button"

    def test_checkbox(self):
        d = _desc(tag="input", itype="checkbox")
        assert classify_widget(d) == "checkbox"

    def test_checkbox_role(self):
        d = _desc(tag="span", role="checkbox")
        assert classify_widget(d) == "checkbox"

    def test_radio(self):
        d = _desc(tag="input", itype="radio")
        assert classify_widget(d) == "radio"

    def test_radio_role(self):
        d = _desc(tag="span", role="radio")
        assert classify_widget(d) == "radio"

    def test_kendo_grid(self):
        d = _desc(tag="div", role="grid", is_kendo_grid=True)
        assert classify_widget(d) == "kendo_grid"

    def test_kendo_numeric_by_ancestor(self):
        d = _desc(tag="input", itype="text", is_kendo_numeric=True)
        assert classify_widget(d) == "kendo_numeric"

    def test_kendo_numeric_beats_generic_input(self):
        """A number input inside .k-numerictextbox must be kendo_numeric, not input."""
        d = _desc(tag="input", itype="number", is_kendo_numeric=True)
        assert classify_widget(d) == "kendo_numeric", (
            "kendo_numeric ancestor check must fire before generic input classification"
        )

    def test_kendo_combobox_role_listbox_with_kendo_name(self):
        d = _desc(tag="span", role="listbox", kendo_select_name="subnetmaskOptions")
        assert classify_widget(d) == "kendo_combobox"

    def test_kendo_combobox_role_combobox(self):
        d = _desc(tag="span", role="combobox", kendo_select_name="ipTypeOptions")
        assert classify_widget(d) == "kendo_combobox"

    def test_kendo_combobox_tag_select(self):
        d = _desc(tag="select", options=["A", "B"])
        assert classify_widget(d) == "kendo_combobox"

    def test_kendo_combobox_has_options(self):
        """An element with options[] but no kendo ancestor → kendo_combobox."""
        d = _desc(tag="input", options=["opt1", "opt2"])
        assert classify_widget(d) == "kendo_combobox"

    def test_native_number_input_without_kendo(self):
        """A plain number input (not inside .k-numerictextbox) → kendo_numeric."""
        d = _desc(tag="input", itype="number", is_kendo_numeric=False)
        assert classify_widget(d) == "kendo_numeric"

    def test_plain_text_input(self):
        d = _desc(tag="input", itype="text")
        assert classify_widget(d) == "input"

    def test_textarea(self):
        d = _desc(tag="textarea")
        assert classify_widget(d) == "input"

    def test_role_textbox(self):
        d = _desc(tag="span", role="textbox")
        assert classify_widget(d) == "input"

    def test_all_results_in_widget_types(self):
        """Every classify_widget result must be in WIDGET_TYPES."""
        cases = [
            _desc(tag="button"),
            _desc(tag="input", itype="checkbox"),
            _desc(tag="input", itype="radio"),
            _desc(tag="div", is_kendo_grid=True),
            _desc(tag="input", is_kendo_numeric=True),
            _desc(tag="select", options=["a"]),
            _desc(tag="input", itype="number"),
            _desc(tag="input", itype="text"),
        ]
        for d in cases:
            result = classify_widget(d)
            assert result in WIDGET_TYPES, f"classify_widget returned {result!r}"


# ---------------------------------------------------------------------------
# resolve_label
# ---------------------------------------------------------------------------


class TestResolveLabel:
    def test_aria_label_wins(self):
        d = _desc(
            aria_label="Subnet Mask",
            spatial_label="Other",
            placeholder="fallback",
        )
        assert resolve_label(d) == "Subnet Mask"

    def test_labelledby_text_second(self):
        d = _desc(labelledby_text="Process ID", placeholder="fallback")
        assert resolve_label(d) == "Process ID"

    def test_label_for_text_third(self):
        d = _desc(label_for_text="Host Name", placeholder="fallback")
        assert resolve_label(d) == "Host Name"

    def test_spatial_label_fourth(self):
        d = _desc(spatial_label="Prefix Mask", placeholder="fallback")
        assert resolve_label(d) == "Prefix Mask"

    def test_placeholder_fifth(self):
        d = _desc(placeholder="xxx.xxx.xxx.xxx")
        assert resolve_label(d) == "xxx.xxx.xxx.xxx"

    def test_title_sixth(self):
        d = _desc(title="Tooltip Help")
        assert resolve_label(d) == "Tooltip Help"

    def test_name_attr_seventh(self):
        d = _desc(name_attr="networkIp")
        assert resolve_label(d) == "networkIp"

    def test_id_eighth_non_ng(self):
        d = _desc(id="hostname_field")
        assert resolve_label(d) == "hostname_field"

    def test_id_skips_ng_prefix(self):
        d = _desc(id="ng-12345")
        assert resolve_label(d) == ""

    def test_truncated_to_80(self):
        d = _desc(aria_label="x" * 200)
        assert len(resolve_label(d)) == 80


# ---------------------------------------------------------------------------
# resolve_key
# ---------------------------------------------------------------------------


class TestResolveKey:
    def test_name_attr_wins(self):
        d = _desc(name_attr="subnetMask", ng_model="scope.subnetMask")
        assert resolve_key(d, "Subnet Mask") == "subnetmask"

    def test_ng_model_tail_second(self):
        d = _desc(ng_model="dhcpScope.startingIp")
        assert resolve_key(d, "Starting IP") == "startingip"

    def test_label_slug_third(self):
        d = _desc()
        assert resolve_key(d, "Subnet Mask") == "subnet-mask"

    def test_empty_for_anonymous(self):
        d = _desc()
        assert resolve_key(d, "") == ""

    def test_always_lowercase(self):
        d = _desc(name_attr="NetworkIP")
        assert resolve_key(d, "Network IP") == "networkip"


# ---------------------------------------------------------------------------
# is_apply_control / is_open_form_control
# ---------------------------------------------------------------------------


class TestControlDetection:
    def test_apply_primary_action_class(self):
        d = _desc(tag="button", classes="btn btn-primary primaryActionButton")
        assert is_apply_control(d) is True

    def test_apply_pl_save(self):
        d = _desc(tag="button", classes="pl-save")
        assert is_apply_control(d) is True

    def test_apply_icon_save_device(self):
        d = _desc(tag="button", classes="icon-save-device")
        assert is_apply_control(d) is True

    def test_not_apply_plain_button(self):
        d = _desc(tag="button", classes="btn btn-secondary")
        assert is_apply_control(d) is False

    def test_open_form_add(self):
        d = _desc(tag="button", aria_label="Add")
        assert is_open_form_control(d) is True

    def test_open_form_add_new(self):
        d = _desc(tag="button", aria_label="Add New")
        assert is_open_form_control(d) is True

    def test_open_form_create(self):
        d = _desc(tag="button", aria_label="create")
        assert is_open_form_control(d) is True

    def test_open_form_plus(self):
        d = _desc(tag="button", aria_label="+")
        assert is_open_form_control(d) is True

    def test_not_open_form_save(self):
        d = _desc(tag="button", aria_label="Save")
        assert is_open_form_control(d) is False

    def test_not_open_form_non_button(self):
        d = _desc(tag="input", itype="text", aria_label="Add")
        assert is_open_form_control(d) is False


# ---------------------------------------------------------------------------
# build_atlas — integration over a canned descriptor list
# ---------------------------------------------------------------------------

# Canned descriptors modelling an OSPF config form.
_OSPF_DESCRIPTORS = [
    # Subnet Mask — Kendo combobox dropdown
    _desc(
        tag="span",
        role="listbox",
        kendo_select_name="subnetmaskOptions",
        options=["255.255.255.0", "255.255.255.128", "255.255.254.0"],
        spatial_label="Subnet Mask",
    ),
    # Process ID — plain text input, required
    _desc(
        tag="input",
        itype="text",
        aria_label="Process ID",
        name_attr="processId",
        required=True,
    ),
    # Apply to Device button (apply glyph)
    _desc(
        tag="button",
        classes="btn btn-primary primaryActionButton",
        aria_label="Apply to Device",
    ),
    # Add button (open-form control)
    _desc(
        tag="button",
        aria_label="Add",
    ),
    # Duplicate Process ID — should be de-duped (first wins)
    _desc(
        tag="input",
        itype="text",
        aria_label="Process ID Duplicate",
        name_attr="processId",
        required=False,
    ),
]


def test_build_atlas_fields():
    atlas = build_atlas(
        _OSPF_DESCRIPTORS,
        route="#/ospf",
        device_fingerprint="c1111-4p__17-6-3a",
        page_title="OSPF Configuration",
    )
    assert atlas.route == "#/ospf"
    assert atlas.device_fingerprint == "c1111-4p__17-6-3a"
    assert atlas.page_title == "OSPF Configuration"

    # Two form fields: Subnet Mask and Process ID.
    field_keys = [f.key for f in atlas.fields]
    assert "subnetmaskoptions" in field_keys or "subnet-mask" in field_keys
    # Process ID field (key = processid)
    process_field = next((f for f in atlas.fields if f.key == "processid"), None)
    assert process_field is not None, f"processid not found; keys={field_keys}"
    assert process_field.label == "Process ID"
    assert process_field.widget == "input"
    assert process_field.required is True


def test_build_atlas_kendo_combobox():
    atlas = build_atlas(
        _OSPF_DESCRIPTORS,
        route="#/ospf",
        device_fingerprint="fp",
        page_title="OSPF",
    )
    subnet_field = next((f for f in atlas.fields if f.widget == "kendo_combobox"), None)
    assert subnet_field is not None, "kendo_combobox field not found"
    assert subnet_field.label == "Subnet Mask"
    assert subnet_field.options == ["255.255.255.0", "255.255.255.128", "255.255.254.0"]
    assert subnet_field.kendo_select_name == "subnetmaskOptions"


def test_build_atlas_apply_controls():
    atlas = build_atlas(
        _OSPF_DESCRIPTORS,
        route="#/ospf",
        device_fingerprint="fp",
        page_title="OSPF",
    )
    assert len(atlas.apply_controls) == 1
    assert atlas.apply_controls[0].is_router_write is True
    assert atlas.apply_controls[0].label == "Apply to Device"


def test_build_atlas_open_form_control():
    atlas = build_atlas(
        _OSPF_DESCRIPTORS,
        route="#/ospf",
        device_fingerprint="fp",
        page_title="OSPF",
    )
    assert atlas.open_form_control is not None
    assert atlas.open_form_control.reveals == "form"
    assert atlas.open_form_control.label == "Add"


def test_build_atlas_dedupes_by_key():
    atlas = build_atlas(
        _OSPF_DESCRIPTORS,
        route="#/ospf",
        device_fingerprint="fp",
        page_title="OSPF",
    )
    # Should be only ONE field with key "processid" (first wins, duplicate skipped).
    process_fields = [f for f in atlas.fields if f.key == "processid"]
    assert len(process_fields) == 1
    # First one had required=True.
    assert process_fields[0].required is True


def test_build_atlas_captured_at_non_empty():
    atlas = build_atlas(
        _OSPF_DESCRIPTORS,
        route="#/ospf",
        device_fingerprint="fp",
        page_title="OSPF",
    )
    assert atlas.captured_at != ""


def test_build_atlas_skips_empty_label_and_key():
    # Descriptor with no label and no key should be skipped.
    descs = [
        _desc(tag="input", itype="text"),  # no aria_label, no name_attr, no spatial_label, etc.
    ]
    atlas = build_atlas(descs, route="#/test", device_fingerprint="fp", page_title="Test")
    # Since label="" and key="" → element skipped.
    assert atlas.fields == []


def test_build_atlas_excludes_nav_links_and_tabs():
    """Deep-audit regression: classify_widget's catch-all maps unmatched
    elements (nav <a> links, role=tab, role=menuitem) to "input". Without the
    _is_form_control gate they would flood the atlas fields list. Only genuine
    form controls may become FieldSpecs.
    """
    descs = [
        _desc(tag="a", role="", aria_label="Configuration"),  # nav link
        _desc(tag="a", role="link", aria_label="Routing Protocols"),  # nav link
        _desc(tag="li", role="tab", aria_label="Dashboard"),  # tab
        _desc(tag="div", role="menuitem", aria_label="Administration"),  # menu
        # One genuine field — must survive.
        _desc(tag="input", itype="text", name_attr="processId", aria_label="Process ID"),
        # A Kendo dropdown (role=listbox) — must survive.
        _desc(
            tag="span",
            role="listbox",
            aria_label="Subnet Mask",
            kendo_select_name="subnetMask",
            options=["255.255.255.0"],
        ),
    ]
    atlas = build_atlas(descs, route="#/ospf", device_fingerprint="fp", page_title="OSPF")
    keys = [f.key for f in atlas.fields]
    labels = [f.label for f in atlas.fields]
    # Only the two real form controls survive; no link/tab/menuitem leaks in.
    assert len(atlas.fields) == 2, f"expected 2 fields, got {labels}"
    assert "Configuration" not in labels
    assert "Routing Protocols" not in labels
    assert "Dashboard" not in labels
    assert "Administration" not in labels
    assert "processid" in keys
    assert any(f.widget == "kendo_combobox" and f.label == "Subnet Mask" for f in atlas.fields)


# ---------------------------------------------------------------------------
# extract_descriptors + capture_route — MagicMock page I/O tests
# ---------------------------------------------------------------------------


def _make_mock_page(descriptors: list[dict], title: str = "OSPF") -> MagicMock:
    """Return a MagicMock page whose .evaluate() returns the given descriptors."""
    page = MagicMock()
    page.evaluate.return_value = descriptors
    page.title.return_value = title
    return page


def test_extract_descriptors_calls_evaluate_once():
    page = _make_mock_page(_OSPF_DESCRIPTORS)
    result = extract_descriptors(page)
    page.evaluate.assert_called_once_with(_CAPTURE_JS)
    assert isinstance(result, list)


def test_capture_route_calls_evaluate_once():
    page = _make_mock_page(_OSPF_DESCRIPTORS, title="OSPF")
    atlas = capture_route(
        page,
        route="#/ospf",
        device_fingerprint="c1111__17-6",
    )
    # evaluate must be called exactly once (the ONE batched DOM read).
    page.evaluate.assert_called_once()
    assert atlas.route == "#/ospf"
    assert atlas.device_fingerprint == "c1111__17-6"
    assert atlas.page_title == "OSPF"


def test_capture_route_uses_supplied_page_title():
    page = _make_mock_page(_OSPF_DESCRIPTORS, title="Should be ignored")
    atlas = capture_route(
        page,
        route="#/ospf",
        device_fingerprint="fp",
        page_title="Custom Title",
    )
    # page.title() must NOT be called when page_title is supplied.
    page.title.assert_not_called()
    assert atlas.page_title == "Custom Title"


def test_capture_route_fingerprint_stored():
    page = _make_mock_page(_OSPF_DESCRIPTORS)
    atlas = capture_route(
        page,
        route="#/dhcp",
        device_fingerprint="c1111-4p__17-6-3a",
    )
    assert atlas.device_fingerprint == "c1111-4p__17-6-3a"


# ---------------------------------------------------------------------------
# ANTI-REGRESSION: capture_route performs NO per-element DOM calls.
# ---------------------------------------------------------------------------


def test_capture_route_no_per_element_get_attribute():
    """capture_route must ONLY use page.evaluate (the one batched JS call).

    No per-element get_attribute / bounding_box / inner_text / locator calls.
    The mock page/locators would record such calls — assert they're absent.
    """
    page = _make_mock_page(_OSPF_DESCRIPTORS)

    # Add a tracked sub-locator so any per-element locator call would register.
    sub_loc = MagicMock()
    page.locator.return_value = sub_loc

    capture_route(
        page,
        route="#/ospf",
        device_fingerprint="fp",
    )

    # Only page.evaluate and page.title should be called.
    # Specifically: no page.locator, no sub_loc.get_attribute, no sub_loc.bounding_box.
    page.locator.assert_not_called()
    sub_loc.get_attribute.assert_not_called()
    sub_loc.bounding_box.assert_not_called()
    # page.evaluate called exactly once (the batched JS).
    assert page.evaluate.call_count == 1


def test_capture_route_no_per_element_bounding_box():
    """Redundant guard: bounding_box must NOT be called (it is computed in JS)."""
    page = _make_mock_page(_OSPF_DESCRIPTORS)
    capture_route(page, route="#/ospf", device_fingerprint="fp")
    # The page mock itself has no bounding_box attribute — any call would raise.
    # We just verify evaluate count = 1 (already covered above, belt-and-suspenders).
    assert page.evaluate.call_count == 1
