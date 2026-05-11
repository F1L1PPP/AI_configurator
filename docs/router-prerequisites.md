# Cisco C1111 Pre-flight Checklist

> Fill this in during the cabled session. The §3 checklist in `PROJECT_PLAN.md`
> is the source of truth; this is the working sheet where you record actual
> results, command outputs, and the WebUI version number that future Playwright
> selectors depend on.
>
> When everything below is checked, you create the `v0.0.1-bootstrap` tag
> manually (hard rule #7 — I never tag).

---

## Session info

| Field | Value |
|---|---|
| Date completed | — |
| Operator | Filip |
| Router model | Cisco C1111 |
| Router serial | — |
| Console cable | — (rollover, USB-to-RJ45) |
| Ethernet patch cable | — |
| Console session software | (PuTTY / TeraTerm / `Get-PnpDevice` for COM port) |
| Dev machine IP | — |

---

## Checklist

### 1. Privilege-15 user exists (or create one)

**Verify (CLI via console):**
```
show running-config | include username
```

**Expected:** at least one line of the form `username <name> privilege 15 ...`

**Actual output:**
```
[paste here]
```

**If missing, create one:**
```
configure terminal
username netadmin privilege 15 algorithm-type sha256 secret <password>
end
write memory
```

- [ ] Privilege-15 user exists
- Name: `_______________`
- Password recorded in: `.env` (key `ROUTER_SSH_PASSWORD` and `ROUTER_WEBUI_PASSWORD`)

---

### 2. HTTP server + HTTPS server enabled

**Verify:**
```
show running-config | include ip http
```

**Expected:**
```
ip http server
ip http secure-server
ip http authentication local
```

**Actual output:**
```
[paste here]
```

**If missing:**
```
configure terminal
ip http server
ip http secure-server
ip http authentication local
end
write memory
```

- [ ] `ip http server` enabled
- [ ] `ip http secure-server` enabled
- [ ] `ip http authentication local` set

---

### 3. At least 30 VTY lines

**Verify:**
```
show running-config | section line vty
```

**Expected:** `line vty 0 30` (or higher upper bound) with `transport input ssh` (or `ssh telnet`)

**Actual output:**
```
[paste here]
```

**If insufficient (default is often `0 4`):**
```
configure terminal
line vty 0 30
 transport input ssh
 login local
end
write memory
```

- [ ] VTY lines 0–30 configured
- [ ] `transport input` includes `ssh`

---

### 4. SSHv2 enabled

**Verify:**
```
show ip ssh
```

**Expected:** `SSH Enabled - version 2.0`, `Authentication timeout: 60 secs`

**Actual output:**
```
[paste here]
```

**If not:**
```
configure terminal
ip ssh version 2
ip ssh time-out 60
end
write memory
```

- [ ] SSH version 2
- [ ] `ip ssh time-out 60`
- [ ] RSA host key exists (`show crypto key mypubkey rsa`) — if not, generate with `crypto key generate rsa modulus 2048`

---

### 5. Management IP reachable from dev machine

**On the router:**
```
show ip interface brief
```
Record the management IP (likely on `GigabitEthernet0/0/0` or a `Vlan1` SVI):

Management IP: `_______________`

**On the dev machine (Windows PowerShell):**
```powershell
ping <management-ip>
Test-NetConnection <management-ip> -Port 22
Test-NetConnection <management-ip> -Port 443
```

- [ ] `ping` succeeds (4/4 replies)
- [ ] TCP 22 (SSH) succeeds
- [ ] TCP 443 (HTTPS) succeeds

---

### 6. Manual WebUI walk

Open `https://<management-ip>` in Chrome. Accept the self-signed cert warning.
Log in with the privilege-15 user from step 1.

Walk these exact paths and confirm each page actually renders (not just the
Dashboard):

- [ ] **Dashboard** — loads after login, shows device info
- [ ] **Configuration → Layer 2 → VLAN** — shows the VLAN list with an "Add" / "+" button
- [ ] **Administration → Device → Device Properties** — shows the hostname field
- [ ] **Administration → Management → HTTP/HTTPS** — confirms HTTP server settings

**If only Dashboard + Monitoring screens are visible** (the failure mode in
`PROJECT_PLAN.md §10` risk register): re-check steps 1–4. Most common cause is
the user account lacking `privilege 15` even though the rest of the config is
right.

---

### 7. Record WebUI version (selectors depend on this)

The WebUI version is displayed in the top-right corner of every WebUI page
(usually `IOS XE 17.x.x` or a build number like `Bengaluru 17.9.4a`).

**WebUI version:** `_______________`
**IOS XE release (from `show version`):** `_______________`
**Build:** `_______________`

This pins which `webui_agent/selectors/iosxe_default.yaml` map we author on
Day 4. If the version changes later, the selector map needs a re-codegen.

---

### 8. Export known-good `running-config` to USB

The bricking guard. Before any Day 3 write touches the device, we have a
clean image to roll back to.

```
copy running-config usbflash0:known-good-YYYYMMDD.cfg
```

Verify on the USB:
```
dir usbflash0:
```

- [ ] File copied
- Filename: `known-good-_______________.cfg`
- Size: `____` bytes
- USB drive label/serial: `_______________`
- USB drive physically removed from the router and stored where: `_______________`

---

### 9. Throwaway SSH + HTTPS probe from dev machine

These are intentionally NOT committed scripts — just one-shot validation that
the agent's transport layer will work on Day 2.

**SSH probe (PowerShell):**
```powershell
ssh netadmin@<management-ip> "show version | i Cisco IOS XE"
```

Expected: prints one line beginning `Cisco IOS XE Software, Version 17.x.x`.

**HTTPS probe:**
```powershell
curl -k https://<management-ip>/ -I
```

Expected: `HTTP/1.1 200 OK` or `301 Moved Permanently` (redirect to login).

- [ ] SSH probe returned a valid version line
- [ ] HTTPS probe returned 2xx or 3xx

---

## Done — handoff to Day 2

When every box above is checked:

1. Update `.env` with the real `ROUTER_HOST`, `ROUTER_SSH_USER`,
   `ROUTER_SSH_PASSWORD`, `ROUTER_WEBUI_USER`, `ROUTER_WEBUI_PASSWORD`,
   `ROUTER_WEBUI_BASE_URL`.
2. Commit this filled-in checklist (without secrets — make sure no passwords
   appear in any pasted output above).
3. Create the bootstrap tag:
   ```powershell
   git tag -a v0.0.1-bootstrap -m "Repo skeleton, CI, settings, logging, GUI foundation, prereqs verified"
   git push origin v0.0.1-bootstrap
   ```
4. Day 2 starts: CLI read layer (Netmiko pool + `show_*` tools + TextFSM
   parsing + action logger).

---

## Notes / issues encountered

(Capture anything weird here — odd firmware quirks, certificate fingerprint,
PSU/temperature warnings, anything that might bite us on a later day.)

```
[free-form notes]
```
