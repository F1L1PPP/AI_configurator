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
    build_locator,
    capture_route,
    classify_widget,
    extract_descriptors,
    is_apply_control,
    is_open_form_control,
    resolve_key,
    resolve_label,
    view_from_descriptors,
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
    name_count: int = 0,
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
    inner_text: str = "",
    value: str = "",
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
        "name_count": name_count,
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
        "inner_text": inner_text,
        "value": value,
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
            inner_text="Button Text",
        )
        assert resolve_label(d) == "Subnet Mask"

    def test_labelledby_text_second(self):
        d = _desc(labelledby_text="Process ID", placeholder="fallback", inner_text="Something")
        assert resolve_label(d) == "Process ID"

    def test_inner_text_third(self):
        """inner_text resolves BEFORE label_for_text and spatial_label."""
        d = _desc(inner_text="Add", label_for_text="Other Label", spatial_label="Spatial")
        assert resolve_label(d) == "Add"

    def test_inner_text_beats_label_for_text(self):
        """inner_text is checked before label_for_text."""
        d = _desc(inner_text="Create", label_for_text="Label For Text", spatial_label="Spatial")
        assert resolve_label(d) == "Create"

    def test_label_for_text_fourth(self):
        """label_for_text wins when no aria_label / labelledby_text / inner_text."""
        d = _desc(label_for_text="Host Name", placeholder="fallback")
        assert resolve_label(d) == "Host Name"

    def test_spatial_label_fifth(self):
        d = _desc(spatial_label="Prefix Mask", placeholder="fallback")
        assert resolve_label(d) == "Prefix Mask"

    def test_placeholder_sixth(self):
        d = _desc(placeholder="xxx.xxx.xxx.xxx")
        assert resolve_label(d) == "xxx.xxx.xxx.xxx"

    def test_title_seventh(self):
        d = _desc(title="Tooltip Help")
        assert resolve_label(d) == "Tooltip Help"

    def test_name_attr_eighth(self):
        d = _desc(name_attr="networkIp")
        assert resolve_label(d) == "networkIp"

    def test_id_ninth_non_ng(self):
        d = _desc(id="hostname_field")
        assert resolve_label(d) == "hostname_field"

    def test_id_skips_ng_prefix(self):
        d = _desc(id="ng-12345")
        assert resolve_label(d) == ""

    def test_truncated_to_80(self):
        d = _desc(aria_label="x" * 200)
        assert len(resolve_label(d)) == 80

    def test_inner_text_empty_falls_through(self):
        """Empty inner_text does not win — resolution continues to next source."""
        d = _desc(inner_text="", spatial_label="Spatial Label")
        assert resolve_label(d) == "Spatial Label"

    def test_spatial_label_equal_to_value_is_rejected(self):
        """P1-spatial-label: a spatial_label equal to the field's own value
        (a Kendo selected-value span leaking) must NOT be returned — resolution
        falls through to the next source."""
        d = _desc(
            spatial_label="255.255.255.0",
            value="255.255.255.0",
            placeholder="Starting IP",
        )
        # spatial_label == value → skipped → placeholder wins.
        assert resolve_label(d) == "Starting IP"

    def test_spatial_label_kept_when_differs_from_value(self):
        """A spatial_label that differs from the value is still used."""
        d = _desc(spatial_label="Subnet Mask", value="255.255.255.0")
        assert resolve_label(d) == "Subnet Mask"

    def test_spatial_label_equal_value_falls_to_name_attr(self):
        """When the value-equal spatial_label is the only soft source, resolution
        falls all the way to name_attr (never returns the bogus value)."""
        d = _desc(spatial_label="OSPF", value="OSPF", name_attr="processField")
        assert resolve_label(d) == "processField"

    def test_inner_text_equal_to_value_is_rejected(self):
        """A Kendo combobox's inner_text IS its selected value ('255.255.255.0',
        'IPV4'), never the label. resolve_label must reject inner_text == value
        so the real form-group label wins — else the field is named/keyed by its
        value (the DHCP Subnet Mask 'element_intercepted' bug)."""
        d = _desc(
            tag="span",
            role="listbox",
            inner_text="255.255.255.0",
            value="255.255.255.0",
            spatial_label="Subnet Mask",
            kendo_select_name="subnetmaskOptions",
        )
        assert resolve_label(d) == "Subnet Mask"


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
    # Subnet Mask — Kendo combobox dropdown (stable identity: kendo_select_name)
    _desc(
        tag="span",
        role="listbox",
        kendo_select_name="subnetmaskOptions",
        options=["255.255.255.0", "255.255.255.128", "255.255.254.0"],
        spatial_label="Subnet Mask",
        inner_text="",
        value="255.255.255.0",
    ),
    # Process ID — plain text input, required (stable identity: name_attr)
    _desc(
        tag="input",
        itype="text",
        aria_label="Process ID",
        name_attr="processId",
        required=True,
        inner_text="",
        value="",
    ),
    # Apply to Device button (apply glyph)
    _desc(
        tag="button",
        classes="btn btn-primary primaryActionButton",
        aria_label="Apply to Device",
        inner_text="Apply to Device",
    ),
    # Add button (open-form control) — labeled by inner_text (no aria_label)
    _desc(
        tag="button",
        inner_text="Add",
    ),
    # Duplicate Process ID — should be de-duped (first wins)
    _desc(
        tag="input",
        itype="text",
        aria_label="Process ID Duplicate",
        name_attr="processId",
        required=False,
        inner_text="",
        value="",
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


# ---------------------------------------------------------------------------
# C5 NEW TESTS — inner_text, stable locator, identity gate, view_from_descriptors
# ---------------------------------------------------------------------------


def test_button_labeled_by_inner_text():
    """A button with inner_text='Add' and no aria/name resolves label='Add'
    and is detected as the open_form_control by build_atlas."""
    # Button: no aria_label, no name_attr, only inner_text="Add"
    add_btn = _desc(tag="button", inner_text="Add")
    # A real field so atlas has something else too
    field = _desc(tag="input", itype="text", name_attr="processId", aria_label="Process ID")

    atlas = build_atlas(
        [add_btn, field],
        route="#/ospf",
        device_fingerprint="fp",
        page_title="OSPF",
    )

    # open_form_control must be set and labeled "Add"
    assert atlas.open_form_control is not None, "open_form_control must not be None"
    assert atlas.open_form_control.label == "Add"
    assert atlas.open_form_control.reveals == "form"


def test_build_atlas_drops_fields_without_stable_identity():
    """A textbox with junk inner_text and NO name/ng-model/kendo_select_name
    must NOT become a FieldSpec (identity gate)."""
    # Junk element: version string "17.6.3a" visible, no stable identity
    junk = _desc(
        tag="input",
        itype="text",
        inner_text="17.6.3a",
        spatial_label="Version",
        # no name_attr, no ng_model, no kendo_select_name
    )
    atlas = build_atlas(
        [junk],
        route="#/test",
        device_fingerprint="fp",
        page_title="Test",
    )
    assert atlas.fields == [], (
        f"Junk element without stable identity must be excluded; got {atlas.fields}"
    )


def test_build_locator_primary_is_css_name():
    """When name_attr is set, primary strategy must be css [name='...']."""
    d = _desc(tag="input", itype="text", name_attr="ospfProcessId", aria_label="Process ID")
    locator = build_locator(d, "textbox", "Process ID")

    assert locator.strategy == "css", f"Expected primary strategy 'css', got {locator.strategy!r}"
    assert locator.value == "[name='ospfProcessId']"
    # get_by_role must appear as a fallback
    fb_strategies = [fb.strategy for fb in locator.fallbacks]
    assert "get_by_role" in fb_strategies, f"get_by_role not in fallbacks: {fb_strategies}"


def test_build_locator_primary_is_css_ng_model_when_no_name():
    """When ng_model is set and name_attr absent, primary must be css [ng-model='...']."""
    d = _desc(tag="input", itype="text", ng_model="ctrl.dhcpScopeName")
    locator = build_locator(d, "textbox", "DHCP Scope Name")

    assert locator.strategy == "css"
    assert locator.value == "[ng-model='ctrl.dhcpScopeName']"
    fb_strategies = [fb.strategy for fb in locator.fallbacks]
    assert "get_by_role" in fb_strategies


def test_build_locator_primary_role_loose_when_no_stable_id():
    """When neither name_attr nor ng_model is set, primary is role_loose
    (lenient substring match — Cisco labels carry icons/whitespace). exact
    get_by_role is retained as a fallback."""
    d = _desc(
        tag="span",
        role="listbox",
        kendo_select_name="subnetmaskOptions",
        spatial_label="Subnet Mask",
    )
    locator = build_locator(d, "listbox", "Subnet Mask")

    # No name_attr or ng_model → primary is lenient role match.
    assert locator.strategy == "role_loose"
    fb_strategies = [fb.strategy for fb in locator.fallbacks]
    assert "get_by_role" in fb_strategies  # exact match retained as fallback
    # kendo CSS locator must appear in fallbacks
    fb_values = [fb.value for fb in locator.fallbacks]
    assert any("subnetmaskOptions" in (v or "") for v in fb_values), (
        f"kendo select fallback not found in {fb_values}"
    )


def test_build_locator_button_role_loose_plus_has_text_fallback():
    """Apply/open-form buttons (no name/ng-model) get a lenient role_loose
    primary + a text-based css fallback — Cisco's 'Apply to Device' button's
    accessible name carries a save icon, so exact get_by_role misses it
    (the apply_failed: unmapped_field from the live smoke)."""
    d = _desc(tag="button", classes="primaryActionButton", inner_text="Apply to Device")
    locator = build_locator(d, "button", "Apply to Device")
    assert locator.strategy == "role_loose"
    css_fallbacks = [fb.value for fb in locator.fallbacks if fb.strategy == "css"]
    assert any("has-text" in (v or "") and "Apply to Device" in (v or "") for v in css_fallbacks), (
        f"text-based css fallback not found in {css_fallbacks}"
    )


# ---------------------------------------------------------------------------
# P1-duplicate-name-identity — name_count disambiguation
# ---------------------------------------------------------------------------


def test_resolve_key_uses_ng_model_when_name_nonunique():
    """Two descriptors sharing name_attr='processID' (name_count=3) but distinct
    ng_model must produce DISTINCT keys (from the ng_model tail), not collide on
    the shared name."""
    type_select = _desc(
        tag="select",
        name_attr="processID",
        name_count=3,
        ng_model="ospfModel.OSPFType",
        options=["OSPF", "OSPFv3"],
    )
    pid_input = _desc(
        tag="input",
        itype="text",
        name_attr="processID",
        name_count=3,
        ng_model="ospfModel.OSPFProcessID",
    )
    k1 = resolve_key(type_select, "Type")
    k2 = resolve_key(pid_input, "Process ID")
    assert k1 == "ospftype"
    assert k2 == "ospfprocessid"
    assert k1 != k2, "duplicated name must not collapse two controls into one key"


def test_resolve_key_unique_name_unchanged():
    """Regression lock: a UNIQUE name (name_count<=1) still keys on the name."""
    d = _desc(name_attr="routerID", name_count=1, ng_model="ospfModel.OSPFRouterID")
    assert resolve_key(d, "Router ID") == "routerid"


def test_resolve_key_nonunique_name_no_ng_model_falls_back_to_name():
    """A non-unique name with NO ng_model still yields the name (better than a
    label slug for de-dup); narrowing happens at locate() time."""
    d = _desc(name_attr="dupName", name_count=2, ng_model="")
    assert resolve_key(d, "Some Label") == "dupname"


def test_build_locator_prefers_ng_model_when_name_nonunique():
    """When name_count>1 and an ng_model exists, primary strategy must be
    css [ng-model=...] (unique), with [name=...] demoted to a fallback."""
    d = _desc(
        tag="input",
        itype="text",
        name_attr="processID",
        name_count=3,
        ng_model="ospfModel.OSPFProcessID",
    )
    locator = build_locator(d, "textbox", "Process ID")
    assert locator.strategy == "css"
    assert locator.value == "[ng-model='ospfModel.OSPFProcessID']"
    # [name=...] must still be present as a fallback handle.
    fb_values = [fb.value for fb in locator.fallbacks]
    assert "[name='processID']" in fb_values


def test_build_locator_keeps_name_primary_when_name_unique():
    """Regression lock (OSPF routerID): a UNIQUE name keeps css [name=...] as
    primary — the duplicate-name path must not touch unique names."""
    d = _desc(
        tag="input",
        itype="text",
        name_attr="routerID",
        name_count=1,
        ng_model="ospfModel.OSPFRouterID",
    )
    locator = build_locator(d, "textbox", "Router ID")
    assert locator.strategy == "css"
    assert locator.value == "[name='routerID']"


def test_build_atlas_nonunique_name_keeps_both_controls():
    """Two controls sharing name='processID' but distinct ng_model must BOTH
    survive build_atlas de-dup (distinct keys), not silently drop one."""
    descs = [
        _desc(
            tag="select",
            role="listbox",
            name_attr="processID",
            name_count=2,
            ng_model="ospfModel.OSPFType",
            kendo_select_name="processID",
            options=["OSPF", "OSPFv3"],
            spatial_label="Type",
        ),
        _desc(
            tag="input",
            itype="text",
            name_attr="processID",
            name_count=2,
            ng_model="ospfModel.OSPFProcessID",
            aria_label="Process ID",
            required=True,
        ),
    ]
    atlas = build_atlas(descs, route="#/ospf", device_fingerprint="fp", page_title="OSPF")
    keys = sorted(f.key for f in atlas.fields)
    assert keys == ["ospfprocessid", "ospftype"], f"both controls must survive; got {keys}"


def test_view_from_descriptors_carries_values_and_keys():
    """view_from_descriptors returns correct keys, labels, and live values."""
    descriptors = [
        _desc(
            tag="span",
            role="listbox",
            kendo_select_name="subnetmaskOptions",
            options=["255.255.255.0", "255.255.255.128"],
            spatial_label="Subnet Mask",
            value="255.255.255.128",
        ),
        _desc(
            tag="input",
            itype="text",
            aria_label="Process ID",
            name_attr="processId",
            required=True,
            value="10",
        ),
    ]

    view = view_from_descriptors(
        descriptors,
        route="#/ospf",
        device_fingerprint="fp",
        page_title="OSPF",
    )

    assert view["route"] == "#/ospf"
    assert view["page_title"] == "OSPF"
    fields = view["fields"]
    keys = [f["key"] for f in fields]

    # Process ID must be keyed by name_attr (processid)
    assert "processid" in keys, f"processid not in {keys}"
    process_field = next(f for f in fields if f["key"] == "processid")
    assert process_field["value"] == "10"
    assert process_field["label"] == "Process ID"
    assert process_field["required"] is True

    # Subnet Mask must be present with correct value
    subnet_field = next((f for f in fields if "subnet" in f["key"] or "mask" in f["key"]), None)
    assert subnet_field is not None, f"no subnet field in {keys}"
    assert subnet_field["value"] == "255.255.255.128"
    assert subnet_field["options"] == ["255.255.255.0", "255.255.255.128"]


# ---------------------------------------------------------------------------
# P0b — grid-row checkbox junk filter (is_kendo_grid, not widget=='kendo_grid')
# ---------------------------------------------------------------------------


def test_build_atlas_drops_grid_row_checkbox():
    """A row-select checkbox inside a .k-grid (is_kendo_grid=True) with NO
    kendo backing select must be dropped from BOTH build_atlas and
    view_from_descriptors.

    classify_widget types it as 'checkbox' (rule 3 beats rule 5 kendo_grid),
    so the old ``widget == 'kendo_grid'`` filter leaked it (DHCP's
    "Monitoring" row-checkbox).  The flag-based filter drops it.
    """
    grid_checkbox = _desc(
        tag="input",
        itype="checkbox",
        role="checkbox",
        ng_model="dataItem.checked",
        is_kendo_grid=True,
        name_attr="",
        spatial_label="Monitoring",
    )
    real_input = _desc(
        tag="input",
        itype="text",
        name_attr="poolName",
        aria_label="Pool Name",
        is_kendo_grid=False,
    )
    descs = [grid_checkbox, real_input]

    atlas = build_atlas(descs, route="#/dhcp", device_fingerprint="fp", page_title="DHCP")
    atlas_keys = [f.key for f in atlas.fields]
    assert atlas_keys == ["poolname"], f"grid checkbox must be dropped; got {atlas_keys}"

    view = view_from_descriptors(descs, route="#/dhcp", device_fingerprint="fp", page_title="DHCP")
    view_keys = [f["key"] for f in view["fields"]]
    assert view_keys == ["poolname"], f"grid checkbox must be dropped from view; got {view_keys}"


def test_build_atlas_keeps_grid_combobox_with_select_name():
    """A Kendo widget that legitimately lives inside a .k-grid but carries a
    kendo_select_name (its backing <select>) must be KEPT — the filter only
    drops in-grid descriptors WITHOUT a backing select.

    (classify_widget types any in-grid element as 'kendo_grid' by precedence;
    the field still survives because it has a backing select, which is the
    invariant this test locks: in-grid + select_name = keep.)
    """
    grid_combobox = _desc(
        tag="span",
        role="listbox",
        kendo_select_name="maskOptions",
        options=["255.255.255.0", "255.255.255.128"],
        is_kendo_grid=True,
        spatial_label="Mask",
    )
    descs = [grid_combobox]

    atlas = build_atlas(descs, route="#/dhcp", device_fingerprint="fp", page_title="DHCP")
    assert len(atlas.fields) == 1, (
        f"grid widget with backing select must be kept; got {atlas.fields}"
    )
    # Its locator must point at the backing select so the adapter can resolve it.
    fb_values = [atlas.fields[0].locator.value] + [
        fb.value for fb in atlas.fields[0].locator.fallbacks
    ]
    assert any("maskOptions" in (v or "") for v in fb_values), (
        f"backing select not reachable via locator: {fb_values}"
    )

    view = view_from_descriptors(descs, route="#/dhcp", device_fingerprint="fp", page_title="DHCP")
    assert len(view["fields"]) == 1, (
        f"grid widget with backing select kept in view; got {view['fields']}"
    )
