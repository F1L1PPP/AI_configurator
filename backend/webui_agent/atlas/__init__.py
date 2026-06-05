"""WebUI atlas package.

Public re-exports for the atlas foundation layer.
"""

from __future__ import annotations

from backend.webui_agent.atlas.fingerprint import device_fingerprint, route_slug, slugify
from backend.webui_agent.atlas.reconcile import (
    INTERACTIVE_ROLES,
    ReconcileResult,
    flatten_interactive,
    normalize_name,
    reconcile,
    roles_equivalent,
)
from backend.webui_agent.atlas.schema import (
    SCHEMA_VERSION,
    WIDGET_TYPES,
    ControlSpec,
    FieldSpec,
    LocatorSpec,
    NavStep,
    RouteAtlas,
    SuccessSignal,
)
from backend.webui_agent.atlas.store import AtlasStore

__all__ = [
    # schema
    "SCHEMA_VERSION",
    "WIDGET_TYPES",
    "LocatorSpec",
    "NavStep",
    "FieldSpec",
    "ControlSpec",
    "SuccessSignal",
    "RouteAtlas",
    # fingerprint
    "slugify",
    "route_slug",
    "device_fingerprint",
    # store
    "AtlasStore",
    # reconcile
    "INTERACTIVE_ROLES",
    "flatten_interactive",
    "normalize_name",
    "roles_equivalent",
    "ReconcileResult",
    "reconcile",
]
