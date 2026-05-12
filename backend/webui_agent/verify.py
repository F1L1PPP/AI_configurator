"""Post-write verification helpers — confirm WebUI changes landed via CLI.

The CLI is the ground truth: the WebUI just edits the running-config that
SSH reads. After every WebUI write, run the corresponding `show` command and
match the expected value. If the WebUI clicked but the CLI doesn't see the
change, the write didn't land — surface immediately.
"""

from __future__ import annotations

import re

from backend.cli_agent.read_tools import show_running_config, show_vlan_brief
from backend.core.logging import get_logger

log = get_logger(__name__)


class VerificationError(RuntimeError):
    """Raised when a post-write CLI check disagrees with the expected state."""


def verify_hostname(expected_name: str) -> bool:
    """Return True iff `hostname <expected_name>` appears in running-config.

    Run AFTER a hostname change has been applied via the WebUI. The CLI's
    `show running-config` is independent of the WebUI session, so this
    catches both "form submitted but config rejected" and "form clicked but
    nothing happened" failure modes.
    """
    cfg = show_running_config()
    pattern = rf"^\s*hostname\s+{re.escape(expected_name)}\s*$"
    found = bool(re.search(pattern, cfg, flags=re.MULTILINE))
    log.info("verify_hostname", expected=expected_name, found=found)
    return found


def verify_vlan_exists(vlan_id: int, name: str | None = None) -> bool:
    """Return True iff the VLAN appears in `show vlan brief`.

    If `name` is provided, also confirm the VLAN's name matches (case-
    insensitive). Used after a WebUI VLAN add to prove the row landed in
    the device's actual VLAN database.

    show_vlan_brief() always returns a list (it normalises non-list /
    unparsed output to []), so we just iterate. An empty list naturally
    falls through to the not-found path at the bottom.
    """
    rows = show_vlan_brief()
    vlan_id_str = str(vlan_id)
    for row in rows:
        if row.get("vlan_id") != vlan_id_str:
            continue
        if name is None:
            log.info("verify_vlan", vlan_id=vlan_id, found=True)
            return True
        if row.get("name", "").upper() == name.upper():
            log.info("verify_vlan", vlan_id=vlan_id, name=name, found=True)
            return True
        log.warning(
            "verify_vlan_name_mismatch",
            vlan_id=vlan_id,
            expected=name,
            got=row.get("name"),
        )
        return False

    log.warning("verify_vlan_not_found", vlan_id=vlan_id)
    return False
