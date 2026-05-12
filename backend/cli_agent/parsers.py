"""TextFSM parsing for Cisco IOS XE show commands.

Uses ntc-templates for structured output. Falls back to the raw string when
no template exists for the command — callers must handle both return types.
"""

from __future__ import annotations

from ntc_templates.parse import parse_output as _ntc_parse

from backend.core.logging import get_logger

log = get_logger(__name__)


def parse(platform: str, command: str, raw_output: str) -> list[dict] | str:
    """Parse raw CLI output using ntc-templates TextFSM templates.

    Returns a list of dicts when a template is found, or the raw string when
    no template is available for (platform, command).
    """
    try:
        result: list[dict] = _ntc_parse(
            platform=platform, command=command, data=raw_output
        )
        if result:
            log.debug("parse_ok", platform=platform, command=command, rows=len(result))
            return result
    except Exception as exc:
        log.debug("parse_no_template", platform=platform, command=command, error=str(exc))

    # No template or empty parse — return raw text.
    return raw_output
