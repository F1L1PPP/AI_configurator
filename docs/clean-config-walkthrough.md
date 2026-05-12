# C1111 Clean-Config → Working WebUI Walkthrough

> The exact sequence of router CLI commands to go from a **freshly reset
> running-config** to a **fully functional WebUI** (showing Configuration +
> Administration menus, not just Monitoring) plus DHCP so the dev laptop can
> reach it.
>
> Use this when you reset the router and need to rebuild from scratch. The
> filled-in evidence sheet lives in [`router-prerequisites.md`](router-prerequisites.md).

---

## **CRITICAL: `privilege level 15` on VTY**

Per Cisco's WebUI documentation:

> *"You need a user with privilege 15 to access the configuration screens on
> Web UI. If the privilege is less than 15, you can access only the Dashboard
> and Monitoring screens on Web UI."*

This is `PROJECT_PLAN.md §10`'s "WebUI prerequisites missing → only Dashboard
visible" risk register entry. **Two places this must be set, both required:**

1. `username <name> privilege 15 ...` — the user account
2. `privilege level 15` under `line vty 0 30` — the login session

If either one is missing, the WebUI hides Configuration + Administration
menus after login. We confirmed this against the live router (2026-05-12):
without VTY `privilege level 15`, only Monitoring shows. After applying it,
all 6 menus appear.

---

## Subnet plan

| Element | Value |
|---|---|
| Router (Vlan1 SVI) | `192.168.10.1/24` |
| DHCP excluded | `192.168.10.1` – `192.168.10.10` (static management) |
| DHCP pool | hands out `192.168.10.11`+ |
| DNS | `8.8.8.8`, `8.8.4.4` |

Pick a different subnet here if your home network already uses `192.168.10.0/24`.

---

## Phase 0 — Console in, skip the initial dialog

```
Would you like to enter the initial configuration dialog? [yes/no]: no
Press RETURN to get started.
Router> enable
Router#
```

---

## Phase 1 — Identity + global housekeeping

```
configure terminal
 hostname c1111-lab
 ip domain name lab.local
 no ip domain lookup
 service password-encryption
 enable secret 0 cisco
 username cisco privilege 15 algorithm-type sha256 secret cisco
```

> Lab credential `cisco`/`cisco` — fine for this isolated lab subnet. **Rotate
> before any non-lab deployment.**

---

## Phase 2 — SSHv2 host key + parameters

```
crypto key generate rsa modulus 2048
ip ssh version 2
ip ssh time-out 60
ip ssh authentication-retries 3
```

If asked "% Do you really want to replace them? [yes/no]:" answer `yes`.

---

## Phase 3 — VTY 30 lines — INCLUDING `privilege level 15`

**This is where yesterday's setup went wrong.** Every command below is
required. The `privilege level 15` line is what unlocks the WebUI's
Configuration + Administration menus:

```
line vty 0 30
 transport input ssh
 login local
 exec-timeout 30 0
 privilege level 15
 exit
```

Verify with `show running-config | section line vty` — the output should
include `privilege level 15` under the vty stanza. If not, re-apply.

---

## Phase 4 — HTTP + HTTPS WebUI

```
ip http server
ip http secure-server
ip http authentication local
ip http max-connections 16
ip http secure-port 443
```

The WebUI takes 30–60 seconds to fully initialize after `ip http
secure-server` — first request will hang briefly while the JS app warms up.

---

## Phase 5 — DHCP server + management IP on Vlan1

```
ip dhcp excluded-address 192.168.10.1 192.168.10.10
ip dhcp pool LAB-CLIENTS
 network 192.168.10.0 255.255.255.0
 default-router 192.168.10.1
 dns-server 8.8.8.8 8.8.4.4
 lease 7
 exit

interface Vlan1
 ip address 192.168.10.1 255.255.255.0
 no shutdown
 exit
```

---

## Phase 6 — Switchports up in VLAN 1

```
interface range GigabitEthernet0/1/0 - 3
 switchport mode access
 switchport access vlan 1
 spanning-tree portfast
 no shutdown
 exit
```

`spanning-tree portfast` skips the STP listen/learn states — saves ~30 s
when you plug your laptop in.

---

## Phase 7 — Save + sanity check

```
end
write memory
show ip interface brief
show ip dhcp pool
show ip http server status
show ip ssh
show running-config | section line vty       ! confirm privilege level 15 is there
show running-config | include privilege       ! double-check
```

Expected hits:
- `Vlan1 192.168.10.1 YES manual up up`
- `HTTP server status: Enabled` + `HTTP secure server status: Enabled`
- `SSH Enabled - version 2.0`
- VTY config includes `privilege level 15`
- `username cisco privilege 15 secret 9 ...`

---

## Phase 8 — On the laptop: get the DHCP lease + verify

Plug Ethernet from laptop NIC to any switchport (`Gi0/1/0`–`Gi0/1/3`).

```powershell
ipconfig | findstr "IPv4"                  # should show 192.168.10.11+
ping 192.168.10.1
Test-NetConnection 192.168.10.1 -Port 22
Test-NetConnection 192.168.10.1 -Port 443
ssh cisco@192.168.10.1 "show version | i Cisco IOS XE"
curl -k https://192.168.10.1/ -I
```

---

## Phase 9 — Open the WebUI and verify all 6 menus appear

1. `https://192.168.10.1` in Chrome
2. Accept the self-signed cert ("Advanced" → "Proceed to 192.168.10.1")
3. Log in `cisco` / `cisco`
4. **The left sidebar must show ALL of these:**
   - Dashboard
   - Monitoring
   - **Configuration** ← if missing, `privilege level 15` is not on VTY
   - **Administration** ← if missing, same fix
   - Licensing
   - Troubleshooting

5. Record the WebUI version from the top of the page into
   `router-prerequisites.md` section 7. For our lab box (2026-05-12) it was
   `Cisco IOS XE Bengaluru 17.6.3a` (`17.06.03a`), image
   `bootflash:c1100-universalk9.17.06.03a.SPA.bin`, model `C1111-4P`.

6. The Playwright verification at
   `playwright_playground/scripts/05_real_router_probe.py` automates this
   walk — runs in 30 s headed, captures 5 screenshots, exits 0 if all 5
   menus are present.

---

## Phase 10 — USB known-good config backup

```
show file systems | include usb
copy running-config usbflash0:known-good-20260512.cfg
dir usbflash0:
```

Unplug the USB, store somewhere separate from the router.

---

## Phase 11 — Populate `.env` on the laptop

```
ROUTER_HOST=192.168.10.1
ROUTER_SSH_USER=cisco
ROUTER_SSH_PASSWORD=cisco
ROUTER_WEBUI_USER=cisco
ROUTER_WEBUI_PASSWORD=cisco
ROUTER_WEBUI_BASE_URL=https://192.168.10.1
```

`.env` is gitignored — never commit it.

---

## Verified on 2026-05-12

| Check | Result |
|---|---|
| Router reachable | ping 0% loss, TTL 255, 1 ms RTT |
| HTTPS WebUI | 401 from `curl -k -I https://192.168.10.1/` (server up, gating on auth) |
| Self-signed cert | `IOS-Self-Signed-Certificate-578219896`, `ignore_https_errors=True` bypasses cleanly |
| SSH from Netmiko (`cisco_xe`) | works, returns full `running-config` |
| WebUI nav after priv-15 fix | Dashboard + Monitoring + Configuration + Administration + Licensing + Troubleshooting (6/6) |
| Configuration submenu | Interface, Layer2 (incl. VLAN), Routing Protocols, Security, Services — all expanded and clickable |
| Playwright login probe | passes — screenshots under `playwright_playground/artifacts/05_real_router_*/` |
