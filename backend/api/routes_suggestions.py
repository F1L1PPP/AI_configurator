"""GET /api/suggestions — context-aware chat suggestion chips via Haiku.

Reads the router's running-config, distils a short digest, and asks
claude-haiku-4-5-20251001 for 4 one-click prompt suggestions.  Results
are cached 30 s per device to avoid hammering the router or the LLM.

Soft-fail by design: every failure path returns _DEFAULT_SUGGESTIONS so
the frontend chips are never empty.
"""

from __future__ import annotations

import re
import time
from typing import Any

from anthropic import Anthropic
from anthropic._exceptions import OverloadedError as AnthropicOverloadedError
from fastapi import APIRouter

from backend.cli_agent import read_tools
from backend.core.logging import get_logger
from backend.core.settings import get_settings

router = APIRouter(prefix="/api", tags=["suggestions"])
log = get_logger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_HAIKU_MAX_TOKENS = 256
_CACHE_TTL_SECONDS = 30
_DIGEST_MAX_CHARS = 2_000  # cap inner-LLM prompt cost

# (device_id -> (expires_at, list[str])) — process-local, single-router lab.
_cache: dict[str, tuple[float, list[str]]] = {}

# Always-safe default chips.  Returned on cache miss + Haiku failure,
# Haiku overload, SSH failure, or empty response.  The frontend ALSO uses
# these as the React initial state so the chat is never empty.
_DEFAULT_SUGGESTIONS: list[str] = [
    "add VLAN 30 named OFFICE",
    "change hostname to LAB-R1",
    "set GigabitEthernet0/1 to 192.168.10.1/24",
    "how do I configure a trunk port?",
]

_INNER_SYSTEM_PROMPT = """\
You generate short next-step prompts for a Cisco IOS XE config agent's
suggestion chips. The operator sees these as one-click chat starters.

Output: 4 short prompts, ONE PER LINE, no numbering, no bullets, no
markdown, no commentary. Each prompt:
- Under 12 words.
- Phrased as a user request (e.g. "add VLAN ..."  / "change hostname to ..." / "show me ...").
- English. No mixed languages.
- Should make sense given the device state below. Avoid suggesting
  things that are already configured (e.g. don't say "create VLAN 30" if
  VLAN 30 already exists; pick a different VLAN number).
- Mix of write actions (configure / set / add) and read actions
  (show / list / how do I).

Return EXACTLY 4 lines. No more, no fewer. No blank lines."""


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------


def _build_digest(running_config: str) -> str:
    """Extract a compact device-state summary from a full running-config.

    Extracts:
    - The ``hostname X`` line (one global line).
    - Each ``vlan N`` stanza header + the ``name X`` line inside, in
      ``vlan_id name`` form.  VLANs without a ``name`` line emit ``vlan N``.
    - Each ``interface <X>`` header + its ``ip address Y Z`` line if present,
      in ``iface ip mask`` form.  Interfaces without an ``ip address`` emit
      ``interface X``.

    The result is capped at _DIGEST_MAX_CHARS to bound LLM prompt cost.
    """
    lines: list[str] = []

    # hostname — one line anywhere in the config
    hostname_match = re.search(r"^hostname\s+(\S+)", running_config, re.MULTILINE)
    if hostname_match:
        lines.append(f"hostname {hostname_match.group(1)}")

    # Walk the config line-by-line to extract VLAN stanzas and interfaces.
    # We track "current block" context so we can associate name/ip address
    # lines with their parent stanza header.
    current_vlan_id: str | None = None
    current_vlan_name: str | None = None
    current_iface: str | None = None
    current_iface_ip: str | None = None

    def _flush_vlan() -> None:
        """Emit accumulated VLAN info and reset state."""
        nonlocal current_vlan_id, current_vlan_name
        if current_vlan_id is not None:
            if current_vlan_name:
                lines.append(f"vlan {current_vlan_id} {current_vlan_name}")
            else:
                lines.append(f"vlan {current_vlan_id}")
        current_vlan_id = None
        current_vlan_name = None

    def _flush_iface() -> None:
        """Emit accumulated interface info and reset state."""
        nonlocal current_iface, current_iface_ip
        if current_iface is not None:
            if current_iface_ip:
                ip_parts = current_iface_ip.split()
                if len(ip_parts) >= 2:
                    lines.append(f"interface {current_iface} {ip_parts[0]} {ip_parts[1]}")
                else:
                    lines.append(f"interface {current_iface}")
            else:
                lines.append(f"interface {current_iface}")
        current_iface = None
        current_iface_ip = None

    for raw_line in running_config.splitlines():
        line = raw_line.rstrip()

        # Skip internal VLAN allocation policy line — not useful for grounding.
        if "vlan internal allocation policy" in line:
            continue

        # Detect top-level stanza headers (no leading whitespace).
        if not line.startswith(" ") and not line.startswith("\t"):
            # Starting a new stanza — flush whatever we were tracking.
            vlan_m = re.match(r"^vlan\s+(\d+)\s*$", line)
            iface_m = re.match(r"^interface\s+(\S+)", line)

            if vlan_m:
                _flush_iface()  # interfaces don't nest inside vlans but flush anyway
                _flush_vlan()
                current_vlan_id = vlan_m.group(1)
                current_vlan_name = None
            elif iface_m:
                _flush_vlan()
                _flush_iface()
                current_iface = iface_m.group(1)
                current_iface_ip = None
            else:
                # Some other top-level block — flush current stanza context.
                _flush_vlan()
                _flush_iface()
        else:
            # Indented line — belongs to current stanza.
            stripped = line.strip()

            if current_vlan_id is not None:
                name_m = re.match(r"^name\s+(.+)$", stripped)
                if name_m:
                    current_vlan_name = name_m.group(1).strip()

            if current_iface is not None and "secondary" not in stripped:
                # Match primary ip address only. Skip "secondary" lines up
                # front: the old negative-lookahead form backtracked and
                # captured a truncated mask (e.g. "255.255.255.") on
                # `ip address X Y secondary` lines.
                ip_m = re.match(r"^ip address\s+(\S+\s+\S+)", stripped)
                if ip_m and current_iface_ip is None:
                    current_iface_ip = ip_m.group(1)

    # Flush last open stanza.
    _flush_vlan()
    _flush_iface()

    digest = "\n".join(lines)
    return digest[:_DIGEST_MAX_CHARS]


# ---------------------------------------------------------------------------
# Haiku call
# ---------------------------------------------------------------------------


def _call_haiku(digest: str) -> list[str]:
    """Return up to 4 suggestion strings.  Raises on API failure."""
    client = Anthropic(api_key=get_settings().anthropic_api_key, max_retries=5)
    response = client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=_HAIKU_MAX_TOKENS,
        system=_INNER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Device state:\n{digest}"}],
    )
    text = "\n".join(
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    ).strip()
    # Parse: one prompt per line, strip whitespace, drop empty.
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Defensive: cap at 4 even if LLM emitted more.
    return raw_lines[:4]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/suggestions")
async def get_suggestions(device_id: str = "router-01") -> dict[str, Any]:
    """Return {"suggestions": [str, ...], "source": "cache" | "fresh" | "fallback"}.

    Soft-fail by design — never returns an HTTP error.  The frontend
    treats this endpoint as best-effort UX enhancement and falls back to
    its own initial state if ``suggestions`` is empty.
    """
    now = time.monotonic()
    cached = _cache.get(device_id)
    if cached is not None:
        expires_at, chips = cached
        if expires_at > now:
            return {"suggestions": chips, "source": "cache"}

    # Cache miss / expired — generate fresh.
    try:
        running_config = read_tools.show_running_config()
    except Exception as exc:
        log.warning("suggestions_show_running_config_failed", error=str(exc))
        return {"suggestions": _DEFAULT_SUGGESTIONS, "source": "fallback"}

    digest = _build_digest(running_config)

    try:
        chips = _call_haiku(digest)
    except AnthropicOverloadedError as exc:
        log.warning("suggestions_haiku_overloaded", request_id=getattr(exc, "request_id", None))
        return {"suggestions": _DEFAULT_SUGGESTIONS, "source": "fallback"}
    except Exception as exc:
        log.warning("suggestions_haiku_failed", error=str(exc))
        return {"suggestions": _DEFAULT_SUGGESTIONS, "source": "fallback"}

    if not chips:
        return {"suggestions": _DEFAULT_SUGGESTIONS, "source": "fallback"}

    _cache[device_id] = (now + _CACHE_TTL_SECONDS, chips)
    return {"suggestions": chips, "source": "fresh"}
