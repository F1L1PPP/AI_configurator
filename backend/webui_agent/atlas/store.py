"""AtlasStore — load/save/overrides for the WebUI atlas.

Atomic writes prevent partial-JSON corruption on crash or Ctrl-C.
Overrides let operators patch field labels, options, and required flags
without re-running a full capture.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from backend.core.logging import get_logger
from backend.webui_agent.atlas.fingerprint import route_slug
from backend.webui_agent.atlas.schema import LocatorSpec, RouteAtlas, SuccessSignal

logger = get_logger(__name__)


class AtlasStore:
    """Filesystem store for :class:`RouteAtlas` objects.

    Directory layout::

        <atlas_dir>/
          <fingerprint>/
            routes/
              dhcp.json
              ospf.json
              ...
            _overrides.json
    """

    def __init__(self, atlas_dir: Path, fingerprint: str) -> None:
        self.atlas_dir = atlas_dir
        self.fingerprint = fingerprint

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @property
    def _device_dir(self) -> Path:
        return self.atlas_dir / self.fingerprint

    @property
    def _routes_dir(self) -> Path:
        return self._device_dir / "routes"

    def _route_path(self, route: str) -> Path:
        return self._routes_dir / f"{route_slug(route)}.json"

    @property
    def _overrides_path(self) -> Path:
        return self._device_dir / "_overrides.json"

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_route(self, atlas: RouteAtlas) -> None:
        """Atomically write *atlas* to disk.

        Uses a ``.tmp`` side-file + :func:`os.replace` so readers never see
        a partial write.
        """
        path = self._route_path(atlas.route)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = path.with_suffix(".json.tmp")
        data = json.dumps(atlas.to_dict(), indent=2, ensure_ascii=False, default=str)
        tmp_path.write_text(data, encoding="utf-8")
        os.replace(tmp_path, path)

        logger.info(
            "atlas_route_saved",
            route=atlas.route,
            field_count=len(atlas.fields),
            path=str(path),
        )

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_overrides(self) -> dict:
        """Return the overrides dict, or ``{}`` if missing/unreadable."""
        path = self._overrides_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        # A syntactically-valid but non-object overrides file (e.g. a top-level
        # JSON array) would make _apply_overrides' .get() raise AttributeError
        # and break load_route's "never raises" contract. Coerce to {}.
        return data if isinstance(data, dict) else {}

    def load_route(self, route: str) -> RouteAtlas | None:
        """Load and return a :class:`RouteAtlas`, or ``None`` on any failure.

        Never raises — parse errors are logged and swallowed.
        Overrides are merged before returning.
        """
        path = self._route_path(route)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            atlas = RouteAtlas.from_dict(raw)
        except Exception as exc:
            logger.warning(
                "atlas_route_load_failed",
                route=route,
                path=str(path),
                error=str(exc),
            )
            return None

        overrides = self.load_overrides()
        atlas = _apply_overrides(atlas, overrides)
        return atlas


# ---------------------------------------------------------------------------
# Override application (pure function — easier to test independently)
# ---------------------------------------------------------------------------

_FIELD_PATCHABLE = frozenset({"label", "required", "options", "value_hint", "locator"})
_PAGE_PATCHABLE = frozenset({"page_title", "success_signal"})


def _apply_overrides(atlas: RouteAtlas, overrides: dict) -> RouteAtlas:
    """Apply the overrides dict to *atlas* and return the (mutated) atlas.

    Override shape::

        {
            "<route_slug>": {
                "page": { "page_title": "...", "success_signal": {...} },
                "fields": {
                    "<field_key>": {
                        "label": "...",
                        "required": true,
                        "options": [...],
                        "value_hint": "...",
                        "locator": {...}
                    }
                }
            }
        }

    Unknown keys are silently ignored.  This function is total — never raises.
    """
    slug = route_slug(atlas.route)
    entry = overrides.get(slug)
    if not entry or not isinstance(entry, dict):
        return atlas

    # --- page-level patches ---
    page_patch = entry.get("page")
    if isinstance(page_patch, dict):
        if "page_title" in page_patch and isinstance(page_patch["page_title"], str):
            atlas.page_title = page_patch["page_title"]
        if "success_signal" in page_patch and isinstance(page_patch["success_signal"], dict):
            with contextlib.suppress(Exception):
                atlas.success_signal = SuccessSignal.from_dict(page_patch["success_signal"])

    # --- field patches ---
    fields_patch = entry.get("fields")
    if not isinstance(fields_patch, dict):
        return atlas

    for field_obj in atlas.fields:
        patch = fields_patch.get(field_obj.key)
        if not isinstance(patch, dict):
            continue
        for attr in _FIELD_PATCHABLE:
            if attr not in patch:
                continue
            val = patch[attr]
            if attr == "locator":
                if isinstance(val, dict):
                    with contextlib.suppress(Exception):
                        field_obj.locator = LocatorSpec.from_dict(val)
            elif attr == "required":
                field_obj.required = bool(val)
            else:
                setattr(field_obj, attr, val)

    return atlas
