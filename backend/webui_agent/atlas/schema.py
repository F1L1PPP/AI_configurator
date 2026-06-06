"""Atlas schema dataclasses.

Provides the data model for the WebUI atlas — a per-device map of each
page's fields and widgets.  All dataclasses round-trip through plain
JSON-able dicts via ``to_dict()`` / ``from_dict()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_VERSION = 1

WIDGET_TYPES: frozenset[str] = frozenset(
    {
        "input",
        "kendo_combobox",
        "kendo_numeric",
        "checkbox",
        "radio",
        "kendo_grid",
        "button",
    }
)


# ---------------------------------------------------------------------------
# LocatorSpec
# ---------------------------------------------------------------------------


@dataclass
class LocatorSpec:
    """Describes how to locate a UI element on the page."""

    strategy: str  # one of: get_by_role, css, ng_model, name
    role: str | None = None
    name: str | None = None
    value: str | None = None
    fallbacks: list[LocatorSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "fallbacks": [f.to_dict() for f in self.fallbacks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> LocatorSpec:
        return cls(
            strategy=d["strategy"],
            role=d.get("role"),
            name=d.get("name"),
            value=d.get("value"),
            fallbacks=[LocatorSpec.from_dict(f) for f in d.get("fallbacks", [])],
        )


# ---------------------------------------------------------------------------
# NavStep
# ---------------------------------------------------------------------------


@dataclass
class NavStep:
    """A single navigation click to reach a page."""

    role: str
    name: str

    def to_dict(self) -> dict:
        return {"role": self.role, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> NavStep:
        return cls(role=d["role"], name=d["name"])


# ---------------------------------------------------------------------------
# FieldSpec
# ---------------------------------------------------------------------------


@dataclass
class FieldSpec:
    """Describes a single form field on a page."""

    key: str
    label: str
    role: str
    widget: str
    required: bool = False
    locator: LocatorSpec | None = None
    options: list[str] | None = None
    kendo_select_name: str | None = None
    value_hint: str | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "role": self.role,
            "widget": self.widget,
            "required": self.required,
            "locator": self.locator.to_dict() if self.locator is not None else None,
            "options": self.options,
            "kendo_select_name": self.kendo_select_name,
            "value_hint": self.value_hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FieldSpec:
        locator_d = d.get("locator")
        return cls(
            key=d["key"],
            label=d["label"],
            role=d["role"],
            widget=d["widget"],
            required=d.get("required", False),
            locator=LocatorSpec.from_dict(locator_d) if locator_d is not None else None,
            options=d.get("options"),
            kendo_select_name=d.get("kendo_select_name"),
            value_hint=d.get("value_hint"),
        )


# ---------------------------------------------------------------------------
# ControlSpec
# ---------------------------------------------------------------------------


@dataclass
class ControlSpec:
    """Describes a control (button/link) on a page."""

    key: str
    label: str
    role: str
    locator: LocatorSpec | None = None
    is_router_write: bool = False
    reveals: str | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "role": self.role,
            "locator": self.locator.to_dict() if self.locator is not None else None,
            "is_router_write": self.is_router_write,
            "reveals": self.reveals,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ControlSpec:
        locator_d = d.get("locator")
        return cls(
            key=d["key"],
            label=d["label"],
            role=d["role"],
            locator=LocatorSpec.from_dict(locator_d) if locator_d is not None else None,
            is_router_write=d.get("is_router_write", False),
            reveals=d.get("reveals"),
        )


# ---------------------------------------------------------------------------
# SuccessSignal
# ---------------------------------------------------------------------------


@dataclass
class SuccessSignal:
    """Describes a UI signal that indicates a successful operation."""

    kind: str  # currently always "a11y_text"
    contains: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "contains": self.contains}

    @classmethod
    def from_dict(cls, d: dict) -> SuccessSignal:
        return cls(kind=d["kind"], contains=d["contains"])


# ---------------------------------------------------------------------------
# RouteAtlas
# ---------------------------------------------------------------------------


@dataclass
class RouteAtlas:
    """Atlas entry for a single WebUI route."""

    route: str
    device_fingerprint: str
    page_title: str = ""
    url_template: str = ""
    nav_click_path: list[NavStep] = field(default_factory=list)
    open_form_control: ControlSpec | None = None
    fields: list[FieldSpec] = field(default_factory=list)
    apply_controls: list[ControlSpec] = field(default_factory=list)
    success_signal: SuccessSignal | None = None
    schema_version: int = SCHEMA_VERSION
    captured_at: str = ""
    captured_by: str = ""
    verify_count: int = 0
    drift_count: int = 0

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "device_fingerprint": self.device_fingerprint,
            "page_title": self.page_title,
            "url_template": self.url_template,
            "nav_click_path": [n.to_dict() for n in self.nav_click_path],
            "open_form_control": (
                self.open_form_control.to_dict() if self.open_form_control is not None else None
            ),
            "fields": [f.to_dict() for f in self.fields],
            "apply_controls": [c.to_dict() for c in self.apply_controls],
            "success_signal": (
                self.success_signal.to_dict() if self.success_signal is not None else None
            ),
            "schema_version": self.schema_version,
            "captured_at": self.captured_at,
            "captured_by": self.captured_by,
            "verify_count": self.verify_count,
            "drift_count": self.drift_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RouteAtlas:
        open_form_d = d.get("open_form_control")
        success_d = d.get("success_signal")
        return cls(
            route=d["route"],
            device_fingerprint=d["device_fingerprint"],
            page_title=d.get("page_title", ""),
            url_template=d.get("url_template", ""),
            nav_click_path=[NavStep.from_dict(n) for n in d.get("nav_click_path", [])],
            open_form_control=(
                ControlSpec.from_dict(open_form_d) if open_form_d is not None else None
            ),
            fields=[FieldSpec.from_dict(f) for f in d.get("fields", [])],
            apply_controls=[ControlSpec.from_dict(c) for c in d.get("apply_controls", [])],
            success_signal=(SuccessSignal.from_dict(success_d) if success_d is not None else None),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            captured_at=d.get("captured_at", ""),
            captured_by=d.get("captured_by", ""),
            verify_count=d.get("verify_count", 0),
            drift_count=d.get("drift_count", 0),
        )

    def field_by_key(self, key: str) -> FieldSpec | None:
        """Return the FieldSpec with the given key, or None if not found."""
        for f in self.fields:
            if f.key == key:
                return f
        return None
