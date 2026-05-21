# Cisco C1111 Pre-flight Checklist

> Filled in during the cabled session 2026-05-12. Router was reset to clean
> config beforehand and built up fresh per `PROJECT_PLAN.md §3` plus a basic
> DHCP server so the dev laptop can reach the WebUI.
>
> All passwords live in `.env` (gitignored), never pasted here.

---

## Session info

| Field | Value |
|---|---|
| Date completed | 2026-05-12 |
| Operator | Filip |
| Router model | Cisco C1111 |
| Router hostname | `c1111-lab` |
| Console cable | Rollover, USB-to-RJ45 |
| Ethernet patch cable | Cat-5e |
| Console session software | PuTTY |
| Dev machine subnet | 192.168.10.0/24 (DHCP from router) |
| Router management IP | 192.168.10.1 (Vlan1 SVI) |

---

## Checklist

### 1. Privilege-15 user exists

- [x] Privilege-15 user exists
- Name: `cisco`
- Password recorded in: `.env` (`ROUTER_SSH_PASSWORD` and `ROUTER_WEBUI_PASSWORD`)
- Verified via `show running-config | include username` — operator confirmed on console.

> **Note (lab-only credential):** `cisco`/`cisco` is fine for this isolated
> lab subnet with no internet exposure. Rotate to a strong credential before
> any non-lab deployment.

### 2. HTTP server + HTTPS server enabled

- [x] `ip http server` enabled
- [x] `ip http secure-server` enabled
- [x] `ip http authentication local` set
- Verified via `show ip http server status` — `HTTP server status: Enabled`,
  `HTTP secure server status: Enabled`, `Authentication: Local`.

### 3. At least 30 VTY lines

- [x] VTY lines 0–30 configured
- [x] `transport input ssh`
- [x] `login local`
- [x] `privilege level 15` on VTY (lands netadmin straight in enable mode)
- Verified via `show running-config | section line vty` — operator confirmed.

### 4. SSHv2 enabled

- [x] SSH version 2
- [x] `ip ssh time-out 60`
- [x] RSA host key present (2048-bit, generated during cabled session)
- Verified via `show ip ssh` — `SSH Enabled - version 2.0`, `Authentication
  timeout: 60 secs`.

### 5. Management IP reachable from dev machine

- [x] `ping 192.168.10.1` — succeeds, 4/4 replies
- [x] `Test-NetConnection 192.168.10.1 -Port 22` — TcpTestSucceeded: True
- [x] `Test-NetConnection 192.168.10.1 -Port 443` — TcpTestSucceeded: True
- Dev laptop received DHCP lease in the 192.168.10.11+ range from the router's
  `LAB-CLIENTS` pool (`.1`–`.10` excluded for static management).

### 6. Manual WebUI walk

- [x] **Dashboard** loads after login, shows device info
- [x] **Configuration → Layer 2 → VLAN** shows the VLAN list with an Add button
- [x] **Administration → Device → Device Properties** shows the hostname field
- All three render correctly — NOT only Dashboard/Monitoring (so the §10 risk
  register's "WebUI prereqs missing" failure mode did not bite us). Priv-15
  user is being honored end-to-end.

### 7. WebUI version recorded

| Field | Value |
|---|---|
| WebUI version (top-right of every page) | _record exact build during Day 4 codegen session — needed to pin `webui_agent/selectors/iosxe_default.yaml`_ |
| IOS XE release (from `show version`) | _record from `show version` — likely 17.x.x_ |
| Build | _same `show version` output_ |

> Day 4 codegen will rerun and pin against whatever exact build is on the box.
> Recording it precisely there (against the live WebUI) is more accurate than
> guessing here.

### 8. Known-good `running-config` exported to USB

- [x] File copied
- Command used: `copy running-config usbflash0:known-good-20260512.cfg`
- Filename: `known-good-20260512.cfg`
- USB drive: physically removed from the router and stored separately (bricking
  guard intact)

### 9. Throwaway SSH + HTTPS probe from dev machine

- [x] SSH probe — `ssh cisco@192.168.10.1 "show version | i Cisco IOS XE"`
  returned a valid version line
- [x] HTTPS probe — `curl -k https://192.168.10.1/ -I` returned a 2xx/3xx response

---

## Done — handoff to Day 2

Every box above is checked. Status:

1. `.env` populated with real values (`ROUTER_HOST=192.168.10.1`,
   `ROUTER_SSH_USER=cisco`, `ROUTER_WEBUI_BASE_URL=https://192.168.10.1`, etc.)
2. This checklist committed (no passwords pasted)
3. `v0.0.1-bootstrap` tag moved forward to the commit that includes this
   filled-in checklist

Day 2 starts: CLI read layer (Netmiko pool + four `show_*` tools + TextFSM
parsing + action logger) + Dashboard "Recent Activity" panel wired to read
`logs/actions.log` for real.

---

## Notes / issues encountered

```
Clean reset of router config beforehand — built up fresh from blank
running-config per the clean-config walkthrough provided in the cabled-session
chat. Standard config: hostname c1111-lab, domain lab.local, RSA 2048 key,
SSH v2 with 60s timeout, VTY 0-30 with transport input ssh and login local,
HTTP/HTTPS servers with local auth, DHCP server on 192.168.10.0/24 (excluding
.1-.10), Vlan1 SVI at 192.168.10.1/24, switchports Gi0/1/0-3 in access VLAN 1
with portfast enabled.

Total time on console: ~30 minutes including the laptop DHCP lease and WebUI
walk. No surprises — clean baseline behaves exactly as planned.
```

---

## Hardware quirk: C1111-4P `Gi0/1/x` ports are L2-only

The four `GigabitEthernet0/1/0` … `GigabitEthernet0/1/3` ports on the C1111-4P
are **hardware switchports** — they are wired to the on-board EtherSwitch
module, not the routing engine. They cannot carry an L3 `ip address` directly.
IOS XE will reject `ip address` on these ports with `% Invalid input detected
at '^' marker.` even when the syntax looks fine.

The `set_interface_ip` write tool inserts `no switchport` ahead of `ip address`
to convert the port to a routed L3 interface when the chassis allows it. On
the C1111-4P that conversion is **not supported on `Gi0/1/x`** — the SSH
session returns cleanly but the IP never lands. Surfaced 2026-05-18 (`Gi0/1/3`
silently rejected during a `set_interface_ip` call); `_check_netmiko_output_
for_errors` and `_verify_running_config` in `backend/cli_agent/write_tools.py`
now catch this and raise `WriteRejectedError` instead of returning success.

**Right pattern for the L2 ports on this chassis** — assign the address to
the SVI, then put the port in that VLAN:

```
interface vlan 40
 ip address 192.168.40.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1/3
 switchport mode access
 switchport access vlan 40
```

Only `Gi0/0/0` (WAN) is a routed L3 port out of the box — that one takes
`ip address` directly as expected.
