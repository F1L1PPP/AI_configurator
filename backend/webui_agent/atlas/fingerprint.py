"""Device fingerprint and route slug helpers.

Dependency-free module — no imports from cli_agent, playwright, or any
other project module.  Everything here is injectable / unit-testable
with plain dicts.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    """Lowercase *s*, replace runs of non-alphanumeric chars with ``-``.

    Returns ``""`` for an empty or all-separator string.

    Examples::

        slugify("C1111-4P")   -> "c1111-4p"
        slugify("17.6.3a")    -> "17-6-3a"
        slugify("")           -> ""
        slugify("---")        -> ""
    """
    lowered = s.lower()
    slugged = _NON_ALNUM_RE.sub("-", lowered)
    return slugged.strip("-")


def route_slug(route: str) -> str:
    """Extract a short, filesystem-safe slug from a WebUI route string.

    Handles the Cisco WebUI hash-router pattern::

        "#/ospf"                        -> "ospf"
        "/webui/#/dhcp"                 -> "dhcp"
        "https://r/webui/#/dhcp"        -> "dhcp"
        "#/dhcp/"                       -> "dhcp"  (trailing slash stripped)
        "#/dhcp?pool=main"              -> "dhcp"  (query stripped)
        "/general"                      -> "general"  (no hash fragment)
        ""                              -> "root"

    The slug is guaranteed non-empty for a non-empty *route*.
    """
    if not route:
        return "root"

    # Strip query string first.
    route_no_query = route.split("?")[0]

    # If there is a hash fragment, use only the part after '#'.
    if "#" in route_no_query:
        fragment = route_no_query.split("#", 1)[1]
        # Strip leading '/' from fragment path.
        fragment = fragment.lstrip("/").rstrip("/")
        if fragment:
            return slugify(fragment) or "root"

    # No hash — use the final path segment.
    path = route_no_query.rstrip("/")
    tail = path.rsplit("/", 1)[-1] if "/" in path else path
    result = slugify(tail)
    return result if result else slugify(route) or "root"


# ---------------------------------------------------------------------------
# Device fingerprint
# ---------------------------------------------------------------------------

_VERSION_TOKEN_RE = re.compile(r"[\d][\d.a-zA-Z_-]*")


def _extract_version_from_image(image_path: str) -> str:
    """Pull a version-looking token from a running-image path like
    ``flash:c1111-universalk9.17.06.03a.SPA.bin``.

    Strategy: find the last token that starts with a digit.
    """
    tokens = re.split(r"[/\\.]", image_path)
    for tok in reversed(tokens):
        if tok and tok[0].isdigit():
            return tok
    return ""


def device_fingerprint(version_info: dict | None) -> str:
    """Build a deterministic fingerprint string from a parsed ``show version`` dict.

    Returns ``"<model_slug>__<version_slug>"`` where each part is produced by
    :func:`slugify`.  Unknown parts fall back to ``"unknown"``.

    The function is **totally safe** — never raises regardless of input shape.

    Model lookup order (first non-empty value wins, case-insensitive key scan):
      1. ``HARDWARE``  (may be a list — take first element)
      2. ``PID``
      3. ``MODEL``
      4. ``model``
      5. ``hardware``

    Version lookup order:
      1. ``VERSION``
      2. ``version``
      3. ``os_version``
      4. ``RUNNING_IMAGE``  (stripped to version-looking token)
    """
    if not version_info:
        return "unknown__unknown"

    # --- model ---
    model_raw = ""
    for key in ("HARDWARE", "PID", "MODEL", "model", "hardware"):
        val = version_info.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            val = val[0] if val else ""
        model_raw = str(val).strip()
        if model_raw:
            break

    # --- version ---
    version_raw = ""
    for key in ("VERSION", "version", "os_version"):
        val = version_info.get(key)
        if val is None:
            continue
        version_raw = str(val).strip()
        if version_raw:
            break

    if not version_raw:
        image = version_info.get("RUNNING_IMAGE", "")
        if image:
            version_raw = _extract_version_from_image(str(image))

    model_slug = slugify(model_raw) or "unknown"
    version_slug = slugify(version_raw) or "unknown"

    return f"{model_slug}__{version_slug}"
