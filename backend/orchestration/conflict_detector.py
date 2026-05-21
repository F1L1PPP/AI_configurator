"""Universal config-block conflict detector.

``find_existing_block(commands, running_config)`` returns the existing stanza
or global config line that the proposed ``commands`` would overwrite, plus
whether the overwrite is a byte-for-byte no-op.  Pure function — no I/O, no
router contact.  Used by every propose tool in tool_registry.py to surface
conflicts at propose time so the operator approves with eyes open.
"""

from __future__ import annotations

import re

from backend.core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_TRIVIAL_LINES = {"exit", "end", "configure terminal"}

# Physical interface header — skip these as anchors (always pre-exist).
# Match the keyword AND require slash-or-digit notation typical of physical ports.
_PHYSICAL_IFACE_RE = re.compile(
    r"^interface\s+(GigabitEthernet|FastEthernet|TenGigabitEthernet|TwentyFiveGigE"
    r"|FortyGigabitEthernet|HundredGigE|Ethernet|Mgmt)\S+\s*$",
    re.IGNORECASE,
)

# Stanza-opening anchors that have indented bodies.  Used only to classify
# shape; running-config matching still happens via the start-of-line regex.
_STANZA_PATTERNS = [
    re.compile(r"^vlan\s+\d+\s*$", re.IGNORECASE),
    re.compile(
        r"^interface\s+(Vlan|Loopback|Tunnel|BDI|Port-channel|Virtual-Template|Dialer)\d+",
        re.IGNORECASE,
    ),
    re.compile(r"^router\s+\w+", re.IGNORECASE),
    re.compile(r"^route-map\s+\S+", re.IGNORECASE),
    re.compile(r"^ip\s+access-list\s+", re.IGNORECASE),
    re.compile(r"^class-map\s+", re.IGNORECASE),
    re.compile(r"^policy-map\s+", re.IGNORECASE),
    re.compile(r"^crypto\s+map\s+\S+", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_stanza(anchor: str) -> bool:
    """Return True when the anchor opens an indented block."""
    return any(p.match(anchor) for p in _STANZA_PATTERNS)


def _normalise_block(text: str) -> str:
    """Strip trailing whitespace per line, drop empty lines, join with newline."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _extract_stanza_block(lines: list[str], start_idx: int) -> str:
    """Collect the anchor + all indented / exit lines that belong to it."""
    collected = [lines[start_idx]]
    for line in lines[start_idx + 1 :]:
        stripped = line.strip()
        if stripped in ("exit", "exit-address-family"):
            collected.append(line)
            continue
        if line and line[0] in (" ", "\t"):
            collected.append(line)
            continue
        # Non-indented, non-exit — end of stanza
        break
    return "\n".join(ln.rstrip() for ln in collected)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_existing_block(
    config_commands: list[str],
    running_config: str,
) -> dict | None:
    """Return conflict info if ``config_commands`` would overwrite an existing block.

    Parameters
    ----------
    config_commands:
        Ordered list of IOS config lines to be applied (as the planner would
        build them).  May include ``configure terminal`` / ``exit`` bookends.
    running_config:
        Full text of the device's running-config (``show running-config``
        output).

    Returns
    -------
    ``None`` when no existing block is found, otherwise::

        {
            "anchor": str,         # matched stanza header / global key
            "block": str,          # full existing block from running-config
            "is_exact_match": bool # True → proposed == existing (no-op write)
        }
    """
    # ------------------------------------------------------------------
    # Step 1: pick the anchor line from config_commands
    # ------------------------------------------------------------------
    anchor: str | None = None
    for raw_line in config_commands:
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.lower() in _TRIVIAL_LINES:
            continue
        if stripped.lower().startswith("no "):
            continue
        if _PHYSICAL_IFACE_RE.match(stripped):
            continue
        anchor = stripped
        break

    if anchor is None:
        log.info(
            "conflict_detector_no_anchor",
            reason="all config_commands lines were trivial / no-prefix / physical-interface",
            commands_count=len(config_commands),
        )
        return None

    # ------------------------------------------------------------------
    # Step 2 & 3: find anchor in running_config
    # ------------------------------------------------------------------
    pattern = re.compile(rf"^{re.escape(anchor)}\s*$", re.MULTILINE | re.IGNORECASE)
    match = pattern.search(running_config)
    if match is None:
        log.info(
            "conflict_detector_no_match",
            anchor=anchor,
            is_stanza=_is_stanza(anchor),
            running_config_chars=len(running_config),
            # Quick fingerprint to help diagnose IOS dialect differences
            # (e.g. anchor not in running-config at all vs anchor present
            # but with trailing tokens that defeat `\s*$`).
            anchor_substring_present=anchor.lower() in running_config.lower(),
        )
        return None

    # ------------------------------------------------------------------
    # Step 4: extract the existing block
    # ------------------------------------------------------------------
    if _is_stanza(anchor):
        lines = running_config.splitlines()
        # Identify which line index the match falls on
        match_line_start = running_config[: match.start()].count("\n")
        existing_block = _extract_stanza_block(lines, match_line_start)
    else:
        # Global single-line: just the matched line, stripped of trailing ws
        existing_block = running_config[match.start() : match.end()].rstrip()

    # ------------------------------------------------------------------
    # Step 5: compute is_exact_match
    # ------------------------------------------------------------------
    proposed_text = "\n".join(config_commands)
    is_exact = _normalise_block(proposed_text) == _normalise_block(existing_block)

    log.info(
        "conflict_detector_match",
        anchor=anchor,
        is_exact_match=is_exact,
        block_chars=len(existing_block),
    )

    return {
        "anchor": anchor,
        "block": existing_block,
        "is_exact_match": is_exact,
    }
