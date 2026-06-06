"""Unit tests for backend/webui_agent/atlas/reconcile.py.

All tests use plain dicts — no Playwright, no MagicMocks.

Key test: test_kendo_listbox_named_by_atlas_label_not_value — verifies
that the ATLAS label ("Subnet Mask") always wins over the live node name,
even when Kendo surfaces the current value ("255.255.255.0") as the node name.
"""

from __future__ import annotations

from backend.webui_agent.atlas.reconcile import (
    INTERACTIVE_ROLES,
    flatten_interactive,
    normalize_name,
    reconcile,
    roles_equivalent,
)
from backend.webui_agent.atlas.schema import (
    ControlSpec,
    FieldSpec,
    RouteAtlas,
)

# ---------------------------------------------------------------------------
# flatten_interactive
# ---------------------------------------------------------------------------


class TestFlattenInteractive:
    def test_empty_snapshot_returns_empty(self):
        assert flatten_interactive({}) == []

    def test_none_snapshot_returns_empty(self):
        assert flatten_interactive(None) == []

    def test_single_interactive_node(self):
        snapshot = {"role": "textbox", "name": "Hostname", "value": "router1"}
        result = flatten_interactive(snapshot)
        assert len(result) == 1
        assert result[0]["role"] == "textbox"
        assert result[0]["name"] == "Hostname"
        assert result[0]["value"] == "router1"

    def test_non_interactive_root_skipped(self):
        snapshot = {"role": "document", "name": "", "children": []}
        assert flatten_interactive(snapshot) == []

    def test_depth_first_pre_order(self):
        snapshot = {
            "role": "document",
            "children": [
                {
                    "role": "textbox",
                    "name": "A",
                    "children": [{"role": "button", "name": "B"}],
                },
                {"role": "checkbox", "name": "C"},
            ],
        }
        result = flatten_interactive(snapshot)
        names = [n["name"] for n in result]
        assert names == ["A", "B", "C"]

    def test_value_defaults_to_empty_string(self):
        snapshot = {"role": "button", "name": "Apply"}
        result = flatten_interactive(snapshot)
        assert result[0]["value"] == ""

    def test_checked_defaults_to_none(self):
        snapshot = {"role": "button", "name": "Apply"}
        result = flatten_interactive(snapshot)
        assert result[0]["checked"] is None

    def test_checked_field_preserved(self):
        snapshot = {"role": "checkbox", "name": "Enable", "checked": True}
        result = flatten_interactive(snapshot)
        assert result[0]["checked"] is True

    def test_none_children_tolerated(self):
        snapshot = {"role": "textbox", "name": "X", "children": None}
        result = flatten_interactive(snapshot)
        assert len(result) == 1

    def test_missing_children_tolerated(self):
        snapshot = {"role": "textbox", "name": "X"}
        result = flatten_interactive(snapshot)
        assert len(result) == 1

    def test_all_interactive_roles_captured(self):
        children = [{"role": role, "name": f"node_{role}"} for role in INTERACTIVE_ROLES]
        snapshot = {"role": "document", "children": children}
        result = flatten_interactive(snapshot)
        assert len(result) == len(INTERACTIVE_ROLES)

    def test_non_interactive_roles_excluded(self):
        snapshot = {
            "role": "document",
            "children": [
                {"role": "heading", "name": "Title"},
                {"role": "paragraph", "name": "Text"},
                {"role": "textbox", "name": "Input"},
            ],
        }
        result = flatten_interactive(snapshot)
        assert len(result) == 1
        assert result[0]["role"] == "textbox"


# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_strip_whitespace(self):
        assert normalize_name("  Hostname  ") == "hostname"

    def test_strip_trailing_star(self):
        assert normalize_name("Hostname *") == "hostname"

    def test_strip_trailing_colon(self):
        assert normalize_name("Hostname:") == "hostname"

    def test_strip_colon_before_star(self):
        # normalize_name strips * first, then :.
        # "Hostname *:" ends in ':', not '*', so only ':' is stripped → "Hostname *"
        # then ' *' is NOT a trailing *, so no second strip. Result: "hostname *"
        # Practical note: real UI labels don't combine both — this is a boundary test.
        assert normalize_name("Hostname *:") == "hostname *"

    def test_casefold(self):
        assert normalize_name("SUBNET MASK") == "subnet mask"

    def test_internal_whitespace_collapsed(self):
        assert normalize_name("Subnet  Mask") == "subnet mask"

    def test_none_returns_empty(self):
        assert normalize_name(None) == ""

    def test_empty_string_returns_empty(self):
        assert normalize_name("") == ""

    def test_required_marker_in_label(self):
        assert normalize_name("Network *") == "network"


# ---------------------------------------------------------------------------
# roles_equivalent
# ---------------------------------------------------------------------------


class TestRolesEquivalent:
    def test_equal_roles(self):
        assert roles_equivalent("textbox", "textbox") is True

    def test_combobox_listbox_equivalent(self):
        assert roles_equivalent("combobox", "listbox") is True
        assert roles_equivalent("listbox", "combobox") is True

    def test_different_non_combo_roles(self):
        assert roles_equivalent("textbox", "checkbox") is False

    def test_combobox_not_equivalent_to_textbox(self):
        assert roles_equivalent("combobox", "textbox") is False


# ---------------------------------------------------------------------------
# reconcile — core Kendo label-not-value test
# ---------------------------------------------------------------------------


class TestReconcileKendoLabel:
    def test_kendo_listbox_named_by_atlas_label_not_value(self):
        """THE KEY TEST.

        Atlas field has role='combobox', label='Subnet Mask'.
        Live node surfaces as role='listbox', name='Subnet Mask' (label match path).
        View field must have label='Subnet Mask' and value='255.255.255.0'.
        """
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="c1111-4p__17-6-3a",
            fields=[
                FieldSpec(
                    key="subnet_mask",
                    label="Subnet Mask",
                    role="combobox",
                    widget="kendo_combobox",
                    options=["255.255.255.0", "255.255.255.128"],
                )
            ],
        )
        live_nodes = [
            {"role": "listbox", "name": "Subnet Mask", "value": "255.255.255.0"},
        ]

        result = reconcile(atlas, live_nodes)

        assert len(result.view["fields"]) == 1
        f = result.view["fields"][0]
        assert f["label"] == "Subnet Mask", (
            f"Expected atlas label 'Subnet Mask', got {f['label']!r}"
        )
        assert f["value"] == "255.255.255.0"
        assert f["key"] == "subnet_mask"

    def test_kendo_value_named_fallback_maps_to_atlas_label(self):
        """Fallback path: live listbox name is the current VALUE, not the label.

        Single unmatched combobox-family atlas field + single unconsumed
        listbox node → deterministic fallback pairing.  Label must still
        come from the atlas, not from the live node name.
        """
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="c1111-4p__17-6-3a",
            fields=[
                FieldSpec(
                    key="subnet_mask",
                    label="Subnet Mask",
                    role="combobox",
                    widget="kendo_combobox",
                    options=["255.255.255.0", "255.255.255.128"],
                )
            ],
        )
        # Live node name is the selected VALUE — the bug trigger.
        live_nodes = [
            {"role": "listbox", "name": "255.255.255.0", "value": "255.255.255.0"},
        ]

        result = reconcile(atlas, live_nodes)

        assert len(result.view["fields"]) == 1
        f = result.view["fields"][0]
        # ATLAS label always wins — must NOT be the value string.
        assert f["label"] == "Subnet Mask", (
            f"Expected 'Subnet Mask' (atlas label), got {f['label']!r} — "
            "field was named by its value instead of the atlas label"
        )
        assert f["value"] == "255.255.255.0"


# ---------------------------------------------------------------------------
# drift detection
# ---------------------------------------------------------------------------


class TestDriftDetection:
    def test_drift_when_required_field_absent_triggers_recapture(self):
        """Required atlas field with no matching live node → drift is True."""
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(
                    key="network_ip",
                    label="Network",
                    role="textbox",
                    widget="input",
                    required=True,
                )
            ],
        )
        live_nodes: list[dict] = []  # nothing on screen

        result = reconcile(atlas, live_nodes)

        assert "network_ip" in result.missing_required
        assert result.drift is True

    def test_no_drift_when_all_required_fields_present(self):
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(
                    key="network_ip",
                    label="Network",
                    role="textbox",
                    widget="input",
                    required=True,
                )
            ],
        )
        live_nodes = [{"role": "textbox", "name": "Network", "value": "10.0.0.0"}]

        result = reconcile(atlas, live_nodes)

        assert result.missing_required == []
        assert result.drift is False

    def test_extra_live_form_field_flagged(self):
        """Live textbox not in atlas → extra_live non-empty and drift True."""
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(
                    key="network_ip",
                    label="Network",
                    role="textbox",
                    widget="input",
                    required=True,
                )
            ],
        )
        live_nodes = [
            {"role": "textbox", "name": "Network", "value": "10.0.0.0"},
            {"role": "textbox", "name": "Unknown Extra Field", "value": ""},
        ]

        result = reconcile(atlas, live_nodes)

        assert len(result.extra_live) == 1
        assert result.extra_live[0]["name"] == "Unknown Extra Field"
        assert result.drift is True

    def test_button_link_not_counted_as_extra(self):
        """Extra live button/link does NOT set drift."""
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(
                    key="network_ip",
                    label="Network",
                    role="textbox",
                    widget="input",
                    required=True,
                )
            ],
        )
        live_nodes = [
            {"role": "textbox", "name": "Network", "value": "10.0.0.0"},
            {"role": "button", "name": "Cancel", "value": ""},
            {"role": "link", "name": "Help", "value": ""},
            {"role": "menuitem", "name": "Menu item", "value": ""},
        ]

        result = reconcile(atlas, live_nodes)

        assert result.extra_live == [], (
            f"Buttons/links/menuitems must not be in extra_live; got {result.extra_live}"
        )
        assert result.drift is False

    def test_optional_missing_field_not_in_missing_required(self):
        """Non-required atlas field with no live match goes to unmapped but NOT missing_required."""
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(
                    key="optional_field",
                    label="Optional",
                    role="textbox",
                    widget="input",
                    required=False,
                )
            ],
        )
        live_nodes: list[dict] = []

        result = reconcile(atlas, live_nodes)

        assert "optional_field" not in result.missing_required
        assert "optional_field" in result.unmapped_fields


# ---------------------------------------------------------------------------
# view structure
# ---------------------------------------------------------------------------


class TestViewStructure:
    def test_view_contains_required_keys(self):
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            page_title="DHCP",
            fields=[],
            apply_controls=[ControlSpec(key="apply", label="Apply to Device", role="button")],
        )
        result = reconcile(atlas, [])

        view = result.view
        assert "route" in view
        assert "page_title" in view
        assert "fields" in view
        assert "apply_controls" in view
        assert "unmapped" in view

    def test_view_route_and_title(self):
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            page_title="DHCP Configuration",
        )
        result = reconcile(atlas, [])
        assert result.view["route"] == "#/dhcp"
        assert result.view["page_title"] == "DHCP Configuration"

    def test_matched_field_carries_options_from_atlas(self):
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(
                    key="mask",
                    label="Subnet Mask",
                    role="combobox",
                    widget="kendo_combobox",
                    options=["255.255.255.0", "255.255.0.0"],
                )
            ],
        )
        live_nodes = [{"role": "combobox", "name": "Subnet Mask", "value": "255.255.255.0"}]
        result = reconcile(atlas, live_nodes)

        f = result.view["fields"][0]
        assert f["options"] == ["255.255.255.0", "255.255.0.0"]

    def test_apply_controls_in_view(self):
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            apply_controls=[ControlSpec(key="apply", label="Apply to Device", role="button")],
        )
        result = reconcile(atlas, [])
        ac = result.view["apply_controls"]
        assert len(ac) == 1
        assert ac[0]["key"] == "apply"
        assert ac[0]["label"] == "Apply to Device"
        assert ac[0]["role"] == "button"

    def test_unmapped_in_view_matches_unmapped_fields(self):
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(key="a", label="A", role="textbox", widget="input"),
                FieldSpec(key="b", label="B", role="textbox", widget="input"),
            ],
        )
        # Only "A" has a live match.
        live_nodes = [{"role": "textbox", "name": "A", "value": ""}]
        result = reconcile(atlas, live_nodes)

        assert "b" in result.view["unmapped"]
        assert "b" in result.unmapped_fields
        assert "a" not in result.view["unmapped"]


# ---------------------------------------------------------------------------
# Multiple fields — ordering and independence
# ---------------------------------------------------------------------------


class TestMultipleFields:
    def test_multiple_fields_all_matched(self):
        atlas = RouteAtlas(
            route="#/dhcp",
            device_fingerprint="fp",
            fields=[
                FieldSpec(key="network_ip", label="Network", role="textbox", widget="input"),
                FieldSpec(
                    key="subnet_mask",
                    label="Subnet Mask",
                    role="combobox",
                    widget="kendo_combobox",
                ),
                FieldSpec(key="pool_name", label="Pool Name", role="textbox", widget="input"),
            ],
        )
        live_nodes = [
            {"role": "textbox", "name": "Network", "value": "10.0.0.0"},
            {"role": "listbox", "name": "Subnet Mask", "value": "255.255.255.0"},
            {"role": "textbox", "name": "Pool Name", "value": "MY_POOL"},
        ]
        result = reconcile(atlas, live_nodes)

        assert len(result.view["fields"]) == 3
        assert result.unmapped_fields == []
        assert result.missing_required == []
        assert result.extra_live == []
        assert result.drift is False

    def test_live_node_consumed_only_once(self):
        """Two atlas fields with the same label only match the first live node."""
        atlas = RouteAtlas(
            route="#/x",
            device_fingerprint="fp",
            fields=[
                FieldSpec(key="a1", label="Hostname", role="textbox", widget="input"),
                FieldSpec(key="a2", label="Hostname", role="textbox", widget="input"),
            ],
        )
        live_nodes = [
            {"role": "textbox", "name": "Hostname", "value": "val1"},
        ]
        result = reconcile(atlas, live_nodes)

        # Only one live node → only one can be matched; second goes to unmapped.
        matched_keys = [f["key"] for f in result.view["fields"]]
        assert len(matched_keys) == 1
        assert len(result.unmapped_fields) == 1


# ---------------------------------------------------------------------------
# Fallback only fires when conditions are exact
# ---------------------------------------------------------------------------


class TestKendoFallbackConditions:
    def test_fallback_does_not_fire_when_two_unmatched_combos(self):
        """Fallback requires EXACTLY ONE unmatched combo field."""
        atlas = RouteAtlas(
            route="#/x",
            device_fingerprint="fp",
            fields=[
                FieldSpec(key="a", label="A", role="combobox", widget="kendo_combobox"),
                FieldSpec(key="b", label="B", role="combobox", widget="kendo_combobox"),
            ],
        )
        # Two unconsumed listbox nodes — ambiguous, fallback must NOT fire.
        live_nodes = [
            {"role": "listbox", "name": "val1", "value": "val1"},
            {"role": "listbox", "name": "val2", "value": "val2"},
        ]
        result = reconcile(atlas, live_nodes)

        # With two unmatched + two unconsumed, neither pass 1 nor pass 2 can match.
        assert len(result.view["fields"]) == 0
        assert len(result.unmapped_fields) == 2

    def test_fallback_does_not_fire_when_two_unconsumed_combo_nodes(self):
        """Fallback requires EXACTLY ONE unconsumed combobox/listbox node."""
        atlas = RouteAtlas(
            route="#/x",
            device_fingerprint="fp",
            fields=[
                FieldSpec(key="a", label="A", role="combobox", widget="kendo_combobox"),
            ],
        )
        # Two listbox nodes with value-like names — ambiguous.
        live_nodes = [
            {"role": "listbox", "name": "val1", "value": "val1"},
            {"role": "listbox", "name": "val2", "value": "val2"},
        ]
        result = reconcile(atlas, live_nodes)

        # Neither can be matched deterministically.
        assert len(result.view["fields"]) == 0
        assert "a" in result.unmapped_fields
