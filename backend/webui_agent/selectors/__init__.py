"""Selector map loader.

Selectors are stored as YAML so they're editable by hand and diffable in
review. Each element entry is a list of strategy dicts; consumers (login,
flows) walk the list and use the first one that resolves. Strategy keys:

    role:  Playwright get_by_role; pair with `name` for ARIA name
    label: Playwright get_by_label; pair with `exact: false` for substring
    text:  Playwright locator(f"text={value}"); regex syntax allowed
    css:   Playwright locator(value) — any CSS selector

Example (login → username field):

    username:
      - role: textbox
        name: Username
      - css: "input[name='username']"
      - css: "input[type='text']"

Add a custom map by dropping a new file `selectors/iosxe_<version>.yaml` and
passing the name to `load_selectors()`. Falls back to `iosxe_default` if no
exact match exists for the router's reported version.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]

SELECTORS_DIR = Path(__file__).parent


@lru_cache(maxsize=4)
def load_selectors(name: str = "iosxe_default") -> dict:
    """Read selectors/<name>.yaml and return the parsed dict.

    Cached by name — load is cheap but repeated YAML parsing is wasteful.
    """
    path = SELECTORS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No selector map at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
