"""Write tools for Cisco IOS XE devices.

Rules (hard — do not relax):
- Every function requires an approved action_id. NotApproved is raised
  immediately if the gate hasn't been cleared; the device is never touched.
- Every user-supplied parameter is validated against a strict grammar
  BEFORE it reaches Netmiko. Cisco IOS swallows newlines as command
  separators, so unvalidated input is a command-injection vector.
- Every send_config_set call sets read_timeout so a hung SSH read can't
  pin a FastAPI worker forever.
- Pre-snapshot fires before the first config command. Post-snapshot fires
  after success. Both land in artifacts/device-snapshots/<action_id>/.
- On any error during the config push: log, mark the action FAILED, re-raise.
  Never auto-retry a write.
"""

from __future__ import annotations

import ipaddress
import re
import time
from pathlib import Path

from backend.cli_agent.connection import pool
from backend.cli_agent.snapshots import take_snapshot
from backend.core.logging import get_logger
from backend.core.settings import get_settings
from backend.orchestration.confirmations import (
    NotApproved,
    is_approved,
    mark_executed,
    mark_failed,
)

log = get_logger(__name__)

# Timeout for any single send_config_set call. Long enough for snapshot-sized
# configs, short enough that a hung SSH session doesn't pin a FastAPI worker
# indefinitely.
CONFIG_READ_TIMEOUT_S = 30

# Cisco IOS hostname grammar: 1-63 chars, must start with letter, then
# letters/digits/hyphens. Rejects newlines, spaces, semicolons — the things
# that turn into command injection.
_HOSTNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,62}$")

# Cisco interface name grammar: letter, then letters/digits/`./:-`.
# Covers GigabitEthernet0/0/1, Vlan10, FastEthernet1/0/2, etc.
_INTERFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9/.:\-]{1,30}$")

# Cisco VLAN name grammar: 1-32 chars; letters/digits/underscores/hyphens.
# Cisco IOS docs say names are alphanumeric "without spaces or punctuation
# other than _ and -". Mirrors the form validation on the frontend.
_VLAN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


# ---------------------------------------------------------------------------
# Validators — raise ValueError on bad input; never reach Netmiko
# ---------------------------------------------------------------------------


def _validate_hostname(name: str) -> None:
    if not isinstance(name, str) or not _HOSTNAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid hostname {name!r}: must be 1-63 chars, start with a "
            "letter, contain only letters/digits/hyphens (RFC 1035 + Cisco)"
        )


def _validate_interface(interface: str) -> None:
    if not isinstance(interface, str) or not _INTERFACE_RE.fullmatch(interface):
        raise ValueError(
            f"invalid interface name {interface!r}: must match "
            "letter + letters/digits/./:/- (e.g. 'GigabitEthernet0/0/1')"
        )


def _validate_ipv4(value: str, kind: str) -> None:
    """Validate an IPv4 dotted-quad (any valid 32-bit address).

    Stricter rules for the specific role (interface host vs. subnet mask)
    live in `_validate_interface_ip_and_mask` below — keeping this one
    permissive matches the way Cisco ACL grammar lets any 32-bit value
    appear (e.g. wildcard masks)."""
    try:
        ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise ValueError(f"invalid IPv4 {kind} {value!r}: {exc}") from exc


def _validate_subnet_mask(mask: str) -> None:
    """Stricter than _validate_ipv4 — must be a contiguous IPv4 subnet
    mask suitable for an interface assignment (not 0.0.0.0, not a non-
    contiguous wildcard). IOS will reject these at apply time; failing
    fast in chat avoids leaving a snapshot+failed-action behind.

    A contiguous mask is N leading ones followed by (32-N) trailing
    zeros. After bitwise inversion, that's (32-N) leading zeros then N
    trailing ones — which is one-less-than-a-power-of-two (the
    `(x & (x+1)) == 0` test). The all-zeros mask is rejected explicitly.
    """
    try:
        m = int(ipaddress.IPv4Address(mask))
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise ValueError(f"invalid subnet mask {mask!r}: {exc}") from exc
    if m == 0:
        raise ValueError(f"invalid subnet mask {mask!r}: 0.0.0.0 is not a valid interface mask")
    inverted = (~m) & 0xFFFFFFFF
    if inverted != 0 and (inverted & (inverted + 1)) != 0:
        raise ValueError(
            f"invalid subnet mask {mask!r}: not contiguous "
            "(use a standard dotted-decimal mask like 255.255.255.0)"
        )


def _validate_interface_ip_and_mask(ip: str, mask: str) -> None:
    """Combined check used by set_interface_ip — IP is a real host
    address, mask is a real contiguous subnet mask, and the IP isn't
    sitting on the network/broadcast of its own subnet."""
    _validate_ipv4(ip, "address")
    _validate_subnet_mask(mask)
    if ip in ("0.0.0.0", "255.255.255.255"):
        raise ValueError(
            f"invalid interface IP {ip!r}: cannot use the wildcard or limited-broadcast address"
        )
    try:
        net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError) as exc:
        raise ValueError(f"invalid IP/mask combo {ip}/{mask}: {exc}") from exc
    host = ipaddress.IPv4Address(ip)
    # /31 and /32 don't have network/broadcast in the usual sense — Cisco
    # allows host addresses there (point-to-point links, loopbacks).
    if net.prefixlen <= 30 and host in (net.network_address, net.broadcast_address):
        raise ValueError(
            f"invalid interface IP {ip}/{mask}: that's the "
            f"{'network' if host == net.network_address else 'broadcast'} "
            "address of the subnet, not a host address"
        )


def _validate_vlan_id(vlan_id: int) -> None:
    if not isinstance(vlan_id, int) or isinstance(vlan_id, bool):
        raise ValueError(f"invalid VLAN id {vlan_id!r}: must be int")
    if not (1 <= vlan_id <= 4094):
        raise ValueError(f"invalid VLAN id {vlan_id}: must be 1..4094")


def _validate_vlan_name(name: str) -> None:
    if not isinstance(name, str) or not _VLAN_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid VLAN name {name!r}: must be 1-32 chars, "
            "letters/digits/_/- only (no spaces or punctuation)"
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _get_conn():
    s = get_settings()
    return pool.get_connection(s.router_host, s.router_ssh_user, s.router_ssh_password)


def _guard(action_id: str) -> None:
    if not is_approved(action_id):
        raise NotApproved(
            f"action_id {action_id!r} has not been approved. "
            "Call POST /api/approve/{action_id} first."
        )


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


def set_hostname(new_name: str, action_id: str) -> dict:
    """Rename the router hostname.

    Requires prior approval. Takes pre+post snapshots around the change.
    Returns a structured result dict with snapshot paths and raw output.

    Raises ValueError on invalid hostname BEFORE any router contact.
    """
    _validate_hostname(new_name)  # before the approval check — fail-fast
    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    try:
        conn = _get_conn()
        output: str = conn.send_config_set(
            [f"hostname {new_name}"],
            read_timeout=CONFIG_READ_TIMEOUT_S,
        )
        ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="set_hostname",
            action_id=action_id,
            error=str(exc),
        )
        raise  # never auto-retry

    # Hostname change alters the router prompt. Invalidate the pooled
    # connection so the next call reconnects and detects the new prompt.
    s = get_settings()
    pool.invalidate(s.router_host, s.router_ssh_user)

    post_dir: Path = take_snapshot(action_id, "post")
    mark_executed(action_id)

    log.info(
        "tool_call",
        tool="set_hostname",
        params={"name": new_name, "action_id": action_id},
        result_summary=f"hostname set to {new_name!r}",
        duration_ms=ms,
    )

    return {
        "tool": "set_hostname",
        "params": {"name": new_name},
        "output": output,
        "snapshot_pre": str(pre_dir),
        "snapshot_post": str(post_dir),
        "duration_ms": ms,
    }


def set_interface_ip(
    interface: str,
    ip: str,
    mask: str,
    action_id: str,
) -> dict:
    """Assign an IP address to an interface and bring it up.

    Example:
        set_interface_ip("GigabitEthernet0/0/0", "10.0.0.1", "255.255.255.0", action_id)

    Requires prior approval. Takes pre+post snapshots.
    Raises ValueError on invalid interface/ip/mask BEFORE any router contact.

    Note: prepends `no switchport` to the config block. On the C1111-4P
    Gi0/1/0..Gi0/1/3 are switchports by default and IOS XE rejects
    `ip address` on a Layer-2 port ("% Invalid input detected"). The
    `no switchport` converts the port to a routed L3 interface
    implicitly. If the port is already routed (e.g. Gi0/0/0 WAN), the
    command is a no-op — safe to send unconditionally.
    """
    _validate_interface(interface)
    _validate_interface_ip_and_mask(ip, mask)
    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    try:
        conn = _get_conn()
        output: str = conn.send_config_set(
            [
                f"interface {interface}",
                " no switchport",
                f" ip address {ip} {mask}",
                " no shutdown",
            ],
            read_timeout=CONFIG_READ_TIMEOUT_S,
        )
        ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="set_interface_ip",
            action_id=action_id,
            error=str(exc),
        )
        raise

    post_dir: Path = take_snapshot(action_id, "post")
    mark_executed(action_id)

    log.info(
        "tool_call",
        tool="set_interface_ip",
        params={"interface": interface, "ip": ip, "mask": mask, "action_id": action_id},
        result_summary=f"{interface} → {ip}/{mask}",
        duration_ms=ms,
    )

    return {
        "tool": "set_interface_ip",
        "params": {"interface": interface, "ip": ip, "mask": mask},
        "output": output,
        "snapshot_pre": str(pre_dir),
        "snapshot_post": str(post_dir),
        "duration_ms": ms,
    }


def set_access_vlan(vlan_id: int, vlan_name: str, action_id: str) -> dict:
    """Create an access VLAN via CLI: `vlan <id>` + `name <name>`.

    Example:
        set_access_vlan(40, "OFFICE", action_id)

    Mirrors `set_hostname` / `set_interface_ip` shape exactly. Validates
    BOTH inputs before the approval check so a bad VLAN id never reaches
    Netmiko. Takes pre/post snapshots around the change.

    Persistence: this writes to the VLAN database in running-config only.
    No `write memory` / `copy running-config startup-config` is issued —
    on reload the VLAN disappears. An opt-in "persist to startup-config"
    step is on the Day-12 backlog; until then operators have to save
    explicitly (via WebUI Administration → Save Configuration, or CLI
    `wr mem`) if they want the change to survive a reboot.

    Note: this only CREATES the VLAN in the VLAN database. Assigning it
    to a switchport (`switchport access vlan <id>` on an interface) is a
    separate operation — out of scope for the §2 scenarios.
    """
    _validate_vlan_id(vlan_id)
    _validate_vlan_name(vlan_name)
    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    try:
        conn = _get_conn()
        output: str = conn.send_config_set(
            [
                f"vlan {vlan_id}",
                f" name {vlan_name}",
            ],
            read_timeout=CONFIG_READ_TIMEOUT_S,
        )
        ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="set_access_vlan",
            action_id=action_id,
            error=str(exc),
        )
        raise

    post_dir: Path = take_snapshot(action_id, "post")
    mark_executed(action_id)

    log.info(
        "tool_call",
        tool="set_access_vlan",
        params={"vlan_id": vlan_id, "vlan_name": vlan_name, "action_id": action_id},
        result_summary=f"vlan {vlan_id} name {vlan_name}",
        duration_ms=ms,
    )

    return {
        "tool": "set_access_vlan",
        "params": {"vlan_id": vlan_id, "vlan_name": vlan_name},
        "output": output,
        "snapshot_pre": str(pre_dir),
        "snapshot_post": str(post_dir),
        "duration_ms": ms,
    }
