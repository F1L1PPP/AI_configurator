"""Unit tests for backend/webui_agent/atlas/schema.py.

Pure-Python, no Playwright, no MagicMocks — exercises round-trip
serialisation and the field_by_key helper.
"""

from __future__ import annotations

from backend.webui_agent.atlas.schema import (
    SCHEMA_VERSION,
    ControlSpec,
    FieldSpec,
    LocatorSpec,
    NavStep,
    RouteAtlas,
    SuccessSignal,
)

# ---------------------------------------------------------------------------
# LocatorSpec round-trip
# ---------------------------------------------------------------------------


def test_locator_spec_round_trip_simple():
    loc = LocatorSpec(strategy="get_by_role", role="textbox", name="Hostname")
    assert LocatorSpec.from_dict(loc.to_dict()) == loc


def test_locator_spec_round_trip_with_fallbacks():
    fallback = LocatorSpec(strategy="css", value="input#hostname")
    loc = LocatorSpec(strategy="get_by_role", role="textbox", name="Hostname", fallbacks=[fallback])
    restored = LocatorSpec.from_dict(loc.to_dict())
    assert restored == loc
    assert len(restored.fallbacks) == 1
    assert restored.fallbacks[0] == fallback


def test_locator_spec_optional_fields_default_none():
    loc = LocatorSpec(strategy="name")
    d = loc.to_dict()
    assert d["role"] is None
    assert d["name"] is None
    assert d["value"] is None
    assert d["fallbacks"] == []
    assert LocatorSpec.from_dict(d) == loc


# ---------------------------------------------------------------------------
# NavStep round-trip
# ---------------------------------------------------------------------------


def test_nav_step_round_trip():
    nav = NavStep(role="menuitem", name="DHCP")
    assert NavStep.from_dict(nav.to_dict()) == nav


# ---------------------------------------------------------------------------
# FieldSpec round-trip
# ---------------------------------------------------------------------------


def test_field_spec_round_trip_minimal():
    fs = FieldSpec(key="hostname", label="Hostname", role="textbox", widget="input")
    assert FieldSpec.from_dict(fs.to_dict()) == fs


def test_field_spec_round_trip_full():
    loc = LocatorSpec(strategy="get_by_role", role="combobox", name="Subnet Mask")
    fs = FieldSpec(
        key="subnet_mask",
        label="Subnet Mask",
        role="combobox",
        widget="kendo_combobox",
        required=True,
        locator=loc,
        options=["255.255.255.0", "255.255.255.128"],
        kendo_select_name="subnetmaskOptions",
        value_hint="Use /24 for most cases",
    )
    restored = FieldSpec.from_dict(fs.to_dict())
    assert restored == fs
    assert restored.locator == loc


def test_field_spec_from_dict_tolerates_missing_optional_keys():
    # Only required keys provided.
    d = {"key": "x", "label": "X", "role": "textbox", "widget": "input"}
    fs = FieldSpec.from_dict(d)
    assert fs.required is False
    assert fs.locator is None
    assert fs.options is None
    assert fs.kendo_select_name is None
    assert fs.value_hint is None


def test_field_spec_from_dict_ignores_unknown_keys():
    d = {
        "key": "x",
        "label": "X",
        "role": "textbox",
        "widget": "input",
        "future_field": "ignored",
    }
    # Should not raise.
    fs = FieldSpec.from_dict(d)
    assert fs.key == "x"


# ---------------------------------------------------------------------------
# ControlSpec round-trip
# ---------------------------------------------------------------------------


def test_control_spec_round_trip():
    ctrl = ControlSpec(
        key="apply",
        label="Apply to Device",
        role="button",
        is_router_write=True,
    )
    assert ControlSpec.from_dict(ctrl.to_dict()) == ctrl


def test_control_spec_with_locator_and_reveals():
    loc = LocatorSpec(strategy="css", value="button.primaryActionButton")
    ctrl = ControlSpec(
        key="create_pool",
        label="Create DHCP Pool",
        role="button",
        locator=loc,
        reveals="dhcp_pool_form",
    )
    restored = ControlSpec.from_dict(ctrl.to_dict())
    assert restored == ctrl
    assert restored.locator == loc


# ---------------------------------------------------------------------------
# SuccessSignal round-trip
# ---------------------------------------------------------------------------


def test_success_signal_round_trip():
    sig = SuccessSignal(kind="a11y_text", contains="Configuration saved")
    assert SuccessSignal.from_dict(sig.to_dict()) == sig


# ---------------------------------------------------------------------------
# RouteAtlas round-trip — full object
# ---------------------------------------------------------------------------


def _make_full_atlas() -> RouteAtlas:
    fallback_loc = LocatorSpec(strategy="css", value="input[name='hostname']")
    primary_loc = LocatorSpec(
        strategy="get_by_role",
        role="textbox",
        name="Hostname",
        fallbacks=[fallback_loc],
    )
    fields = [
        FieldSpec(
            key="hostname",
            label="Hostname",
            role="textbox",
            widget="input",
            required=True,
            locator=primary_loc,
        ),
        FieldSpec(
            key="subnet_mask",
            label="Subnet Mask",
            role="combobox",
            widget="kendo_combobox",
            options=["255.255.255.0", "255.255.255.128"],
            kendo_select_name="subnetmaskOptions",
        ),
    ]
    apply_ctrl = ControlSpec(
        key="apply",
        label="Apply to Device",
        role="button",
        is_router_write=True,
    )
    open_ctrl = ControlSpec(
        key="create",
        label="Create Pool",
        role="button",
        reveals="pool_form",
    )
    nav = [NavStep(role="menuitem", name="DHCP")]
    sig = SuccessSignal(kind="a11y_text", contains="saved successfully")
    return RouteAtlas(
        route="#/dhcp",
        device_fingerprint="c1111-4p__17-6-3a",
        page_title="DHCP Configuration",
        url_template="https://{host}/webui/#/dhcp",
        nav_click_path=nav,
        open_form_control=open_ctrl,
        fields=fields,
        apply_controls=[apply_ctrl],
        success_signal=sig,
        captured_at="2026-06-04T10:00:00Z",
        captured_by="vision_capture_v1",
        verify_count=2,
        drift_count=0,
    )


def test_route_atlas_round_trip():
    atlas = _make_full_atlas()
    d = atlas.to_dict()
    restored = RouteAtlas.from_dict(d)

    assert restored.route == atlas.route
    assert restored.device_fingerprint == atlas.device_fingerprint
    assert restored.page_title == atlas.page_title
    assert restored.schema_version == SCHEMA_VERSION
    assert len(restored.fields) == 2
    assert len(restored.apply_controls) == 1
    assert restored.success_signal is not None
    assert restored.success_signal.contains == "saved successfully"
    assert len(restored.nav_click_path) == 1
    assert restored.nav_click_path[0].name == "DHCP"
    assert restored.open_form_control is not None
    assert restored.open_form_control.reveals == "pool_form"


def test_route_atlas_nested_locator_fallbacks_preserved():
    atlas = _make_full_atlas()
    restored = RouteAtlas.from_dict(atlas.to_dict())
    hostname_field = restored.field_by_key("hostname")
    assert hostname_field is not None
    assert hostname_field.locator is not None
    assert len(hostname_field.locator.fallbacks) == 1
    assert hostname_field.locator.fallbacks[0].strategy == "css"


def test_route_atlas_from_dict_tolerates_missing_optional_keys():
    # Minimal dict — only required fields.
    d = {"route": "#/ospf", "device_fingerprint": "c1111-4p__17-6-3a"}
    atlas = RouteAtlas.from_dict(d)
    assert atlas.route == "#/ospf"
    assert atlas.page_title == ""
    assert atlas.fields == []
    assert atlas.apply_controls == []
    assert atlas.success_signal is None
    assert atlas.open_form_control is None
    assert atlas.schema_version == SCHEMA_VERSION


def test_route_atlas_from_dict_ignores_unknown_keys():
    d = {
        "route": "#/ospf",
        "device_fingerprint": "c1111-4p__17-6-3a",
        "future_key": "some future value",
    }
    atlas = RouteAtlas.from_dict(d)
    assert atlas.route == "#/ospf"


# ---------------------------------------------------------------------------
# field_by_key helper
# ---------------------------------------------------------------------------


def test_field_by_key_returns_correct_field():
    atlas = _make_full_atlas()
    fs = atlas.field_by_key("subnet_mask")
    assert fs is not None
    assert fs.label == "Subnet Mask"


def test_field_by_key_returns_none_for_missing():
    atlas = _make_full_atlas()
    assert atlas.field_by_key("nonexistent") is None


def test_field_by_key_first_match():
    # Verify it returns the first field with the matching key.
    atlas = _make_full_atlas()
    assert atlas.field_by_key("hostname") is not None


# ---------------------------------------------------------------------------
# RouteAtlas with None success_signal and open_form_control
# ---------------------------------------------------------------------------


def test_route_atlas_none_optional_fields_round_trip():
    atlas = RouteAtlas(
        route="#/general",
        device_fingerprint="c1111-4p__17-6-3a",
        success_signal=None,
        open_form_control=None,
    )
    d = atlas.to_dict()
    assert d["success_signal"] is None
    assert d["open_form_control"] is None
    restored = RouteAtlas.from_dict(d)
    assert restored.success_signal is None
    assert restored.open_form_control is None


# ---------------------------------------------------------------------------
# SCHEMA_VERSION is embedded correctly
# ---------------------------------------------------------------------------


def test_schema_version_embedded_in_to_dict():
    atlas = RouteAtlas(route="#/x", device_fingerprint="fp")
    assert atlas.to_dict()["schema_version"] == SCHEMA_VERSION


def test_schema_version_constant_is_1():
    assert SCHEMA_VERSION == 1
