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
from datetime import UTC, datetime
from pathlib import Path

from backend.cli_agent.connection import pool
from backend.cli_agent.snapshots import take_snapshot
from backend.core.eventbus import bus
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


class WriteRejectedError(RuntimeError):
    """Raised when an approved write didn't actually land on the device.

    Two failure modes converge here:
      1. IOS XE returned a `% ...` error line during the config push but the
         SSH session itself returned cleanly (e.g. `ip address` on a hardware
         L2-only switchport — Netmiko sees a clean return, the agent thinks
         it succeeded, the router silently rejected the command).
      2. The post-write `show running-config` / `show vlan brief` did not
         contain the expected change (no_op write, race-rolled-back change,
         or a feature licence gate the agent didn't know about).

    On both paths: a forensic post-snapshot is captured, the action is
    marked FAILED, and the exception propagates. Per CLAUDE.md "On any
    error during the config push: log, mark the action FAILED, re-raise.
    Never auto-retry a write." Recovery is a separate human-approved action.
    """


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
# CLI AI configure — denylist + validators for inner-LLM-drafted commands
# ---------------------------------------------------------------------------
#
# propose_cli_configure lets the inner Haiku draft raw IOS XE configuration
# commands. Filip approves them through the normal HITL gate, but defense
# in depth means a server-side denylist runs at propose time AND again at
# execute time. Anything in the denylist refuses BEFORE the human sees the
# preview — keeps the slip-of-the-thumb APPROVE-on-reload class of bug off
# the table.
#
# Pattern + reason pairs; we reject on the first match. Order is for
# readability, not priority.
_CONFIG_CMD_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*reload\b", re.IGNORECASE), "reload reboots the router"),
    (re.compile(r"^\s*erase\b", re.IGNORECASE), "erase wipes configuration or flash"),
    (re.compile(r"^\s*delete\b", re.IGNORECASE), "delete removes filesystem objects"),
    (re.compile(r"^\s*format\b", re.IGNORECASE), "format wipes flash"),
    (re.compile(r"^\s*write\s+erase\b", re.IGNORECASE), "write erase wipes startup-config"),
    (re.compile(r"^\s*boot\s+system\b", re.IGNORECASE), "boot system changes the boot image"),
    (
        re.compile(r"^\s*enable\s+(password|secret)\b", re.IGNORECASE),
        "enable password/secret would let the LLM grant itself privileged access",
    ),
    (
        re.compile(r"^\s*username\b.*\bprivilege\b", re.IGNORECASE),
        "username privilege would let the LLM grant itself privileged access",
    ),
    (re.compile(r"[\n;]"), "embedded newline or semicolon (injection vector)"),
]


def _validate_config_commands(commands: object) -> None:
    """Reject inner-LLM-drafted config commands that would brick the
    router, escalate privilege, or smuggle multiple commands through one
    list entry. Raises ValueError on the first offending command.

    Called twice in defense-in-depth: once at propose time so the human
    never sees a dangerous preview, and again at execute time so a
    tampered action dict can't slip past the gate.
    """
    if not isinstance(commands, list) or not commands:
        raise ValueError("config_commands must be a non-empty list of strings")
    for i, cmd in enumerate(commands):
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError(f"config_commands[{i}] must be a non-empty string")
        for pattern, reason in _CONFIG_CMD_DENYLIST:
            if pattern.search(cmd):
                raise ValueError(f"config_commands[{i}] {cmd!r} rejected: {reason}")


def _validate_verify_command(cmd: object) -> None:
    """verify_command runs via send_command in EXEC mode (not config
    mode), so a malicious inner LLM could otherwise smuggle reload/erase
    through the verify slot. Lock it to `show ...` and block injection
    characters.
    """
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("verify_command must be a non-empty string")
    if not re.match(r"^\s*show\s+", cmd, re.IGNORECASE):
        raise ValueError(f"verify_command {cmd!r} must start with 'show '")
    if re.search(r"[\n;]", cmd):
        raise ValueError(f"verify_command {cmd!r} contains newline or semicolon")


def _validate_verify_pattern(pattern: object) -> None:
    """verify_pattern must be a compilable Python regex. Catches the
    obvious "inner LLM emitted a fenced-code-block string" failure mode
    before runtime hits re.search.
    """
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("verify_pattern must be a non-empty string")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"verify_pattern {pattern!r} not a valid regex: {exc}") from exc


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


def _emit_cli_commands(
    tool: str,
    action_id: str,
    commands: list[str],
    mode: str = "config",
) -> None:
    """Publish one `cli_command_sent` event per CLI line so the chat live
    event stream can show what the agent is typing at the IOS prompt.

    Netmiko's `send_config_set` is a single round-trip — there's no actual
    per-line wire delay we can hook into without rewriting the SSH layer.
    Emitting the events here, just before the call, gives the operator
    line-by-line visibility into the same buffered batch. Cosmetic
    line-by-line pacing is applied client-side (see ChatProvider in
    app.jsx).

    `mode` is `"config"` for `send_config_set` calls and `"exec"` for
    `send_command` / post-write verify reads.
    """
    total = len(commands)
    ts_iso = datetime.now(UTC).isoformat()
    for idx, cmd in enumerate(commands, start=1):
        bus.publish(
            {
                "type": "cli_command_sent",
                "ts": ts_iso,
                "data": {
                    "tool": tool,
                    "action_id": action_id,
                    "command": cmd,
                    "command_index": idx,
                    "command_total": total,
                    "mode": mode,
                },
            }
        )


def _check_netmiko_output_for_errors(output: str) -> None:
    """Raise WriteRejectedError if Netmiko config output contains IOS XE
    error markers ('%' line prefix). IOS XE returns from the SSH session
    cleanly even when individual config commands are rejected — so a clean
    `send_config_set` return is NOT proof the change landed. Scan the
    buffered output to surface silent rejections.

    Mirrors the `%`-line extraction in `cli_configure` so the same family
    of failure (e.g. router-id-in-use, ip-on-switchport, malformed param)
    surfaces consistently across fast-path and AI-drafted writes.
    """
    errors = [line.strip() for line in (output or "").splitlines() if line.strip().startswith("%")]
    if errors:
        raise WriteRejectedError(
            "device rejected one or more config commands: " + " | ".join(errors)
        )


def _verify_running_config(verify_command: str, verify_pattern: str, tool: str) -> str:
    """Re-fetch the device state and assert `verify_pattern` matches.

    `verify_command` is a `show ...` query run in EXEC mode (Netmiko's
    send_config_set already exits config mode). `verify_pattern` is a
    Python regex matched with `re.MULTILINE` so `^` / `$` work line-wise.

    Raises WriteRejectedError on SSH failure during verify OR on regex
    miss. Returns the verify output on success (for inclusion in the
    tool's result dict).
    """
    # action_id isn't in scope here — emit with '-' so the live stream
    # still shows what the verify is running, even if it can't cross-
    # reference back to a specific action.
    _emit_cli_commands(tool, "-", [verify_command], mode="exec")
    try:
        conn = _get_conn()
        verify_output: str = conn.send_command(verify_command, read_timeout=60)
    except Exception as exc:
        raise WriteRejectedError(f"{tool} post-write verify SSH read failed: {exc}") from exc

    if not re.search(verify_pattern, verify_output, re.MULTILINE):
        raise WriteRejectedError(
            f"{tool} post-write verify missed: `{verify_command}` did not "
            f"contain expected change (pattern={verify_pattern!r}, "
            f"output preview={verify_output[:200]!r})"
        )
    return verify_output


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

    cmds = [f"hostname {new_name}"]
    _emit_cli_commands("set_hostname", action_id, cmds, mode="config")
    try:
        conn = _get_conn()
        output: str = conn.send_config_set(cmds, read_timeout=CONFIG_READ_TIMEOUT_S)
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
    # connection so the verify read (and any subsequent caller) reconnects
    # against the new prompt.
    s = get_settings()
    pool.invalidate(s.router_host, s.router_ssh_user)

    try:
        _check_netmiko_output_for_errors(output)
        _verify_running_config(
            verify_command="show running-config | include hostname",
            verify_pattern=rf"^hostname {re.escape(new_name)}\s*$",
            tool="set_hostname",
        )
    except WriteRejectedError as exc:
        post_dir = take_snapshot(action_id, "post")  # preserve forensic diff
        mark_failed(action_id)
        log.error(
            "write_rejected",
            tool="set_hostname",
            action_id=action_id,
            snapshot_post=str(post_dir),
            error=str(exc),
        )
        raise

    ms = int((time.monotonic() - t0) * 1000)
    post_dir = take_snapshot(action_id, "post")
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

    cmds = [
        f"interface {interface}",
        " no switchport",
        f" ip address {ip} {mask}",
        " no shutdown",
    ]
    _emit_cli_commands("set_interface_ip", action_id, cmds, mode="config")
    try:
        conn = _get_conn()
        output: str = conn.send_config_set(cmds, read_timeout=CONFIG_READ_TIMEOUT_S)
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="set_interface_ip",
            action_id=action_id,
            error=str(exc),
        )
        raise

    try:
        _check_netmiko_output_for_errors(output)
        _verify_running_config(
            verify_command=f"show running-config interface {interface}",
            verify_pattern=rf"ip address {re.escape(ip)} {re.escape(mask)}",
            tool="set_interface_ip",
        )
    except WriteRejectedError as exc:
        post_dir = take_snapshot(action_id, "post")  # preserve forensic diff
        mark_failed(action_id)
        log.error(
            "write_rejected",
            tool="set_interface_ip",
            action_id=action_id,
            snapshot_post=str(post_dir),
            error=str(exc),
        )
        raise

    ms = int((time.monotonic() - t0) * 1000)
    post_dir = take_snapshot(action_id, "post")
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

    cmds = [
        f"vlan {vlan_id}",
        f" name {vlan_name}",
    ]
    _emit_cli_commands("set_access_vlan", action_id, cmds, mode="config")
    try:
        conn = _get_conn()
        output: str = conn.send_config_set(cmds, read_timeout=CONFIG_READ_TIMEOUT_S)
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="set_access_vlan",
            action_id=action_id,
            error=str(exc),
        )
        raise

    try:
        _check_netmiko_output_for_errors(output)
        # `show vlan brief` row format: `<id>   <name>   active   <ports>`
        # Width-padded but whitespace-separated, so \s+ between fields.
        _verify_running_config(
            verify_command="show vlan brief",
            verify_pattern=rf"^\s*{vlan_id}\s+{re.escape(vlan_name)}\s+active",
            tool="set_access_vlan",
        )
    except WriteRejectedError as exc:
        post_dir = take_snapshot(action_id, "post")  # preserve forensic diff
        mark_failed(action_id)
        log.error(
            "write_rejected",
            tool="set_access_vlan",
            action_id=action_id,
            snapshot_post=str(post_dir),
            error=str(exc),
        )
        raise

    ms = int((time.monotonic() - t0) * 1000)
    post_dir = take_snapshot(action_id, "post")
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


def cli_configure(
    action_id: str,
    config_commands: list[str],
    verify_command: str,
    verify_pattern: str,
) -> dict:
    """Execute a previously-approved CLI config plan drafted by the inner Haiku.

    Validators re-run server-side at execute time (defense in depth) so a
    tampered action dict can't slip a `reload` through after approval.
    Pushes the config block via Netmiko, runs the verify command in EXEC
    mode, and confirms via ``re.search(verify_pattern, verify_output)``.

    On verify miss: post-snapshot captured (so the diff is preserved),
    action marked FAILED, no auto-rollback. Per CLAUDE.md §76, recovery
    is a separate human-approved action.

    Raises ValueError on validator rejection BEFORE any router contact.
    """
    # Re-validate at execute time — first gate runs at propose time, but
    # a tampered action dict could otherwise slip through here.
    _validate_config_commands(config_commands)
    _validate_verify_command(verify_command)
    _validate_verify_pattern(verify_pattern)

    _guard(action_id)

    t0 = time.monotonic()
    pre_dir: Path = take_snapshot(action_id, "pre")

    _emit_cli_commands("cli_configure", action_id, config_commands, mode="config")
    try:
        conn = _get_conn()
        config_output: str = conn.send_config_set(
            config_commands,
            read_timeout=CONFIG_READ_TIMEOUT_S,
        )
    except Exception as exc:
        mark_failed(action_id)
        log.error(
            "write_failed",
            tool="cli_configure",
            action_id=action_id,
            error=str(exc),
        )
        raise  # never auto-retry

    # Verify in EXEC mode. send_command isn't config mode, so we don't
    # need to drop out — Netmiko's send_config_set already exited config
    # by the time it returned.
    _emit_cli_commands("cli_configure", action_id, [verify_command], mode="exec")
    try:
        verify_output: str = conn.send_command(verify_command, read_timeout=60)
    except Exception as exc:
        # The write itself succeeded but verify SSH failed. Capture the
        # post-snapshot anyway so Filip can compare against pre, then
        # surface the failure.
        post_dir = take_snapshot(action_id, "post")
        mark_failed(action_id)
        log.error(
            "cli_configure_verify_ssh_failed",
            action_id=action_id,
            verify_command=verify_command,
            error=str(exc),
        )
        return {
            "error": "verify_ssh_failed",
            "tool": "cli_configure",
            "config_output": config_output,
            "snapshot_pre": str(pre_dir),
            "snapshot_post": str(post_dir),
            "message": str(exc),
        }

    ms = int((time.monotonic() - t0) * 1000)
    post_dir = take_snapshot(action_id, "post")

    match = re.search(verify_pattern, verify_output)
    if match is None:
        # Surface any device-reported errors from the config push. IOS XE
        # marks rejected commands with a leading '%' (e.g. "% Router-ID
        # 10.0.0.1 in use by ospf process 2"). Pull these out so the
        # operator sees WHY verify missed — config silently rejected is a
        # common pattern with router-id / IP / VLAN conflicts.
        device_errors = [
            line.strip()
            for line in (config_output or "").splitlines()
            if line.strip().startswith("%")
        ]
        log.error(
            "cli_configure_verify_failed",
            action_id=action_id,
            verify_command=verify_command,
            verify_pattern=verify_pattern,
            output_preview=verify_output[:300],
            device_errors=device_errors or None,
        )
        # Human-readable message for chat surface. The route handler at
        # routes_approvals.py:222 reads `message` to build the HTTP detail;
        # without it the user sees "no message" and has no idea what went
        # wrong. Surface the verify_command + pattern, plus any device-side
        # `%` errors (e.g. "% Router-ID 10.0.0.1 in use by ospf process 2")
        # which usually pinpoint the real cause.
        if device_errors:
            message = (
                f"Device rejected the config: {'; '.join(device_errors)}. "
                f"Verify `{verify_command}` did not match `{verify_pattern}`."
            )
        else:
            output_snippet = verify_output[:400].replace("\n", " | ").strip()
            message = (
                f"Verify `{verify_command}` ran but its output did not match "
                f"`{verify_pattern}`. Output preview: {output_snippet!r}"
            )
        result: dict = {
            "error": "verify_failed",
            "message": message,
            "tool": "cli_configure",
            "verify_command": verify_command,
            "verify_pattern": verify_pattern,
            "verify_output_preview": verify_output[:3000],
            "config_output": config_output,
            "device_errors": device_errors,
            "snapshot_pre": str(pre_dir),
            "snapshot_post": str(post_dir),
            "duration_ms": ms,
        }
        # Pass result to mark_failed so debug_sweep can retrieve it later
        # via get_action(action_id)["result"]. Without this, the action
        # ends up FAILED with result=None and the auto-debug fallback in
        # tool_registry.find_most_recent_failure filters it out, degrading
        # the reactive diagnostic plan to a generic broad sweep.
        mark_failed(action_id, result)
        return result

    mark_executed(action_id)

    # An approved config block can include prompt-affecting commands (the
    # denylist permits `hostname`). Drop the pooled connection so the next
    # caller reconnects against the current prompt instead of a stale cached
    # base_prompt. Mirrors set_hostname's invalidate.
    s = get_settings()
    pool.invalidate(s.router_host, s.router_ssh_user)

    log.info(
        "tool_call",
        tool="cli_configure",
        params={"action_id": action_id, "command_count": len(config_commands)},
        result_summary=f"applied {len(config_commands)} commands; verify matched",
        duration_ms=ms,
    )

    return {
        "tool": "cli_configure",
        "params": {
            "command_count": len(config_commands),
            "verify_command": verify_command,
        },
        "config_output": config_output,
        "verify_output_preview": verify_output[:1000],
        "verify_matched": True,
        "snapshot_pre": str(pre_dir),
        "snapshot_post": str(post_dir),
        "duration_ms": ms,
    }
