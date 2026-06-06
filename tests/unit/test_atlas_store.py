"""Unit tests for backend/webui_agent/atlas/store.py.

Uses pytest's tmp_path fixture — no live filesystem side-effects.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.webui_agent.atlas.schema import (
    FieldSpec,
    RouteAtlas,
    SuccessSignal,
)
from backend.webui_agent.atlas.store import AtlasStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_atlas(route: str = "#/dhcp", fingerprint: str = "c1111-4p__17-6-3a") -> RouteAtlas:
    return RouteAtlas(
        route=route,
        device_fingerprint=fingerprint,
        page_title="DHCP Configuration",
        fields=[
            FieldSpec(
                key="network_ip",
                label="Network",
                role="textbox",
                widget="input",
                required=True,
            ),
            FieldSpec(
                key="subnet_mask",
                label="Subnet Mask",
                role="combobox",
                widget="kendo_combobox",
                options=["255.255.255.0", "255.255.255.128"],
            ),
        ],
        success_signal=SuccessSignal(kind="a11y_text", contains="saved"),
    )


def _make_store(tmp_path: Path, fingerprint: str = "c1111-4p__17-6-3a") -> AtlasStore:
    return AtlasStore(atlas_dir=tmp_path / "webui_atlas", fingerprint=fingerprint)


# ---------------------------------------------------------------------------
# save → load round-trip
# ---------------------------------------------------------------------------


def test_save_then_load_round_trips(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    assert loaded.route == "#/dhcp"
    assert loaded.page_title == "DHCP Configuration"
    assert len(loaded.fields) == 2
    assert loaded.fields[0].key == "network_ip"
    assert loaded.success_signal is not None
    assert loaded.success_signal.contains == "saved"


def test_save_creates_nested_dirs(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    # Check that webui_atlas/<fingerprint>/routes/ was created.
    routes_dir = tmp_path / "webui_atlas" / "c1111-4p__17-6-3a" / "routes"
    assert routes_dir.is_dir()
    assert (routes_dir / "dhcp.json").exists()


def test_save_atomic_no_tmp_left_behind(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    routes_dir = tmp_path / "webui_atlas" / "c1111-4p__17-6-3a" / "routes"
    tmp_files = list(routes_dir.glob("*.tmp"))
    assert tmp_files == [], f"unexpected .tmp files left behind: {tmp_files}"


def test_save_overwrites_existing(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    # Save again with different page_title.
    atlas2 = RouteAtlas(
        route="#/dhcp",
        device_fingerprint="c1111-4p__17-6-3a",
        page_title="Updated Title",
    )
    store.save_route(atlas2)

    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    assert loaded.page_title == "Updated Title"


# ---------------------------------------------------------------------------
# load missing / corrupt
# ---------------------------------------------------------------------------


def test_load_missing_route_returns_none(tmp_path: Path):
    store = _make_store(tmp_path)
    result = store.load_route("#/nonexistent")
    assert result is None


def test_load_corrupt_json_returns_none_no_raise(tmp_path: Path):
    store = _make_store(tmp_path)
    # Manually create a corrupt JSON file.
    path = store._route_path("#/dhcp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid JSON !!!", encoding="utf-8")

    result = store.load_route("#/dhcp")
    assert result is None  # must not raise


def test_load_partial_json_returns_none_no_raise(tmp_path: Path):
    store = _make_store(tmp_path)
    path = store._route_path("#/ospf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"route": "#/ospf"', encoding="utf-8")  # truncated

    result = store.load_route("#/ospf")
    assert result is None


# ---------------------------------------------------------------------------
# Multiple routes / multiple fingerprints
# ---------------------------------------------------------------------------


def test_different_routes_stored_separately(tmp_path: Path):
    store = _make_store(tmp_path)
    dhcp_atlas = _make_atlas(route="#/dhcp")
    ospf_atlas = RouteAtlas(
        route="#/ospf",
        device_fingerprint="c1111-4p__17-6-3a",
        page_title="OSPF Configuration",
    )
    store.save_route(dhcp_atlas)
    store.save_route(ospf_atlas)

    loaded_dhcp = store.load_route("#/dhcp")
    loaded_ospf = store.load_route("#/ospf")
    assert loaded_dhcp is not None and loaded_dhcp.page_title == "DHCP Configuration"
    assert loaded_ospf is not None and loaded_ospf.page_title == "OSPF Configuration"


def test_different_fingerprints_isolated(tmp_path: Path):
    atlas_dir = tmp_path / "webui_atlas"
    store_a = AtlasStore(atlas_dir=atlas_dir, fingerprint="c1111-4p__17-6-3a")
    store_b = AtlasStore(atlas_dir=atlas_dir, fingerprint="isr4331__16-12-4")

    store_a.save_route(_make_atlas(route="#/dhcp", fingerprint="c1111-4p__17-6-3a"))

    # Device B should not see device A's routes.
    assert store_b.load_route("#/dhcp") is None


# ---------------------------------------------------------------------------
# Overrides — patch field label + options
# ---------------------------------------------------------------------------


def test_overrides_patch_field_label_and_options(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    # Write an overrides file that renames "network_ip" and changes its options.
    overrides = {
        "dhcp": {
            "fields": {
                "network_ip": {
                    "label": "Network Address (Overridden)",
                    "required": False,
                },
                "subnet_mask": {
                    "options": ["255.255.0.0", "255.0.0.0"],
                },
            }
        }
    }
    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text(json.dumps(overrides), encoding="utf-8")

    loaded = store.load_route("#/dhcp")
    assert loaded is not None

    network_field = loaded.field_by_key("network_ip")
    assert network_field is not None
    assert network_field.label == "Network Address (Overridden)"
    assert network_field.required is False  # overridden from True

    subnet_field = loaded.field_by_key("subnet_mask")
    assert subnet_field is not None
    assert subnet_field.options == ["255.255.0.0", "255.0.0.0"]


def test_overrides_win_over_saved_values(tmp_path: Path):
    """Overrides applied LAST always win over saved atlas data."""
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    overrides = {
        "dhcp": {
            "fields": {
                "network_ip": {"label": "Override Label Wins"},
            }
        }
    }
    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text(json.dumps(overrides), encoding="utf-8")

    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    assert loaded.field_by_key("network_ip").label == "Override Label Wins"


def test_overrides_patch_page_title(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    overrides = {
        "dhcp": {
            "page": {"page_title": "DHCP (Override)"},
        }
    }
    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text(json.dumps(overrides), encoding="utf-8")

    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    assert loaded.page_title == "DHCP (Override)"


def test_overrides_patch_success_signal(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    overrides = {
        "dhcp": {
            "page": {"success_signal": {"kind": "a11y_text", "contains": "overridden signal"}},
        }
    }
    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text(json.dumps(overrides), encoding="utf-8")

    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    assert loaded.success_signal is not None
    assert loaded.success_signal.contains == "overridden signal"


def test_overrides_locator_replaced(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    overrides = {
        "dhcp": {
            "fields": {
                "network_ip": {
                    "locator": {
                        "strategy": "css",
                        "value": "input[name='networkIp']",
                        "fallbacks": [],
                    }
                }
            }
        }
    }
    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text(json.dumps(overrides), encoding="utf-8")

    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    field = loaded.field_by_key("network_ip")
    assert field is not None
    assert field.locator is not None
    assert field.locator.strategy == "css"
    assert field.locator.value == "input[name='networkIp']"


def test_overrides_missing_file_no_effect(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    # No _overrides.json present — load must succeed normally.
    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    assert loaded.field_by_key("network_ip").label == "Network"


def test_overrides_non_dict_json_does_not_crash(tmp_path: Path):
    """A valid-JSON but non-object overrides file must not break load_route.

    Regression for the deep-audit finding: a top-level JSON array would make
    _apply_overrides' .get() raise AttributeError, violating load_route's
    documented "never raises" contract. It must be treated as no overrides.
    """
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    # Saved values are returned unchanged (no override applied).
    assert loaded.field_by_key("network_ip").label == "Network"


def test_overrides_unknown_keys_ignored(tmp_path: Path):
    store = _make_store(tmp_path)
    atlas = _make_atlas()
    store.save_route(atlas)

    overrides = {
        "dhcp": {
            "fields": {
                "network_ip": {
                    "label": "Good key",
                    "FUTURE_UNKNOWN_KEY": "should be ignored",
                }
            }
        }
    }
    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text(json.dumps(overrides), encoding="utf-8")

    # Must not raise.
    loaded = store.load_route("#/dhcp")
    assert loaded is not None
    assert loaded.field_by_key("network_ip").label == "Good key"


def test_load_overrides_returns_empty_dict_when_file_missing(tmp_path: Path):
    store = _make_store(tmp_path)
    assert store.load_overrides() == {}


def test_load_overrides_returns_empty_dict_on_corrupt_file(tmp_path: Path):
    store = _make_store(tmp_path)
    store._overrides_path.parent.mkdir(parents=True, exist_ok=True)
    store._overrides_path.write_text("not json", encoding="utf-8")
    assert store.load_overrides() == {}
