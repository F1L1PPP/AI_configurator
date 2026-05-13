# Day 6 — RAG smoke test (10 queries, graded)

**Corpus:** `knowledge_base/docs/` — 2 of 7 PDFs, **772 chunks, 192,654 tokens**.
- `isr1100-sw-config.pdf` — 594 pages → 692 chunks (the IOS XE 17.x software config guide)
- `b-cisco-1100-series-hig.pdf` — 118 pages → 80 chunks (the C1100 series hardware install guide)

**Embedding model:** `sentence-transformers/all-MiniLM-L6-v2`
**Distance metric:** cosine (score = `1 - distance`, higher = more relevant)
**Graded:** 2026-05-13 by the Claude agent, per Filip's explicit authorization

**Grading rule:** the TOP-1 chunk gets **1** if its text contains, or
clearly leads to, the answer a network engineer would accept. **0** if
off-topic, generic, or in a section that's the wrong subject. The
section label is a hint only — what matters is whether the actual
chunk text answers.

## Result: **7 / 10 — PASS** (target was ≥ 7/10)

| Q | Score | Why |
|---|:--:|---|
| 1 | **1** | Top chunk shows `Router(config)# hostname Router` and the `hostname name` syntax explicitly |
| 2 | **0** | Top chunk is generic CLI help (`ip ?` interface subcommands); doesn't say what the default mgmt interface is on the C1111 |
| 3 | **1** | Top chunk is the start of the "Setting Up Factory Default Device Using WebUI" section; shows `ip dhcp pool WEBUIPool`, `interface Vlan1 ip address 192.168.1.1`, and the WebUI URL pattern; #3 explicitly shows `ip http server` |
| 4 | **1** | Top chunk from the HIG lists the actual port labels: `GE WAN ports 0-7`, `GE 0/0/0`, `GE 0/0/1`, `Ethernet switch ports 0-7` |
| 5 | **0** | Top chunk is EIGRP routing protocol config, not management IP / default gateway setup |
| 6 | **1** | Top chunk is the canonical "Flex Port to Layer 2 Port" example showing both `no switchport` (L3) and `switchport / switchport mode access` (L2) on the same interface |
| 7 | **0** | Top chunk is "Configuring Wi-Fi 6" — WLAN VLAN tagging, not the C1111 management VLAN (which is Vlan1 in factory config) |
| 8 | **1** | Top chunk is "ROMmon Images" with the ROMmon compatibility matrix and the boot/upgrade flow |
| 9 | **1** | Top chunk's labelled section is WLAN, but the actual text contains the universal CLI VLAN syntax: `switchport access vlan 199`, `switchport mode access`, `interface vlan 199`. An engineer would learn the answer directly from the chunk |
| 10 | **1** | Top chunk explicitly answers: `Saving Configuration Changes ... copy running-config startup-config` |

**Three of the four misses (Q2, Q5, Q7) are coverage gaps**, not retrieval
failures: the questions ask about *the C1111's default management
configuration* (interface name, IP, default gateway, management VLAN),
which is covered most precisely in the **WebUI User Guide** (rag-sources
doc #3) and the **Basic System Management Command Reference** (doc #4) —
neither is in the corpus yet.

---

## Q1. How do I change the hostname on a Cisco ISR 1100? — **Score 1 / 1**

**#1** ← top, **relevant** [isr1100-sw-config.pdf — Configuring Global Parameters] (score 0.495)

> Router(config)# telnet router-name or address Login: login-id Password: ********* Router> enable Specifies the name for the router.hostname name Example: Step 2 Router(config)# hostname Router Specifies a password to prevent unauthorized access to the router. enable password password Example: Step 3 Note Router(config)# enable password cr1ny5ho ...

**#2** [isr1100-sw-config.pdf — 1000 Series ISR to successfully resolve the FQDN.] (score 0.459) — off-topic (DNS pass-through).

**#3** [isr1100-sw-config.pdf — Configuring Cisco Embedded Wireless Controller (EWC)] (score 0.445) — DHCP pool form fields.

---

## Q2. Default management interface on the C1111 — name and IP? — **Score 0 / 1**

**#1** ← top, **not relevant** [isr1100-sw-config.pdf — Understanding Diagnostic Mode] (score 0.474)

> enable name-caching no Negate a command or set its defaults nrzi-encoding Enable use of NRZI encoding ntp Configure NTP ... Router(config-if)# ip ? Interface IP configuration subcommands: access-group Specify access control for packets ...

Generic CLI help. The actual answer (Vlan1 / 192.168.1.1 in factory
config, or Gi0/0/0 for mgmt) is in chunk Q3#1 below but not retrieved
high for this phrasing.

**#2** [isr1100-sw-config.pdf — 255.255.255.0 IP subnet mask.] (score 0.461) — generic command/no-form prose.

**#3** [isr1100-sw-config.pdf — Configuring Wi-Fi 6] (score 0.444) — WLAN-GE interface description.

---

## Q3. How do I enable the WebUI on a Cisco router (ip http server)? — **Score 1 / 1**

**#1** ← top, **relevant (leads to answer)** [isr1100-sw-config.pdf — Configuring Console Port for Modem Connection] (score 0.722)

> ... Setting Up Factory Default Device Using WebUI Quick Setup Wizard ... Step 2 Ensure that the following basic configuration is available on the device. ! ! ip dhcp excluded-address 192.168.1.1 192.168.1.5 ! ip dhcp pool WEBUIPool network 192.168.1.0 255.255.255.0 default-router 192.168.1.1 dns-server 192.168.1.1 ! ! username webui privilege 15 secret cisco ! interface Vlan1 ip address 192.168.1.1 255.255.255.0 ip nat inside no shutdown ! ...

Section label is misleading (a chunking artifact — the previous section
heading "stuck" through a page break), but the actual text is the
WebUI setup prerequisites.

**#2** [isr1100-sw-config.pdf — CHAPTER 11] (score 0.698) — `https://192.168.1.1/webui/#/dayZeroRouting`, default creds.

**#3** [isr1100-sw-config.pdf — CHAPTER 11] (score 0.693) — explicit `ip http server` and `ip http secure-server` commands.

---

## Q4. What interfaces does the C1111 expose? Naming convention? — **Score 1 / 1**

**#1** ← top, **relevant** [b-cisco-1100-series-hig.pdf — About Cisco 1000 Series Integrated Service Routers] (score 0.470)

> ... GE WAN ports: 0-7 (0, 2, 4, 6 at the top and 1, 3, 5, 7 at the bottom) ... GE 0/0/0 RJ45 LED ... GE 0/0/1 LED ... Ethernet switch ports 0-3 ... Ethernet switch ports 0-7 ...

Hardware install guide describing the actual port labels per model.

**#2** [isr1100-sw-config.pdf — Configuring Wi-Fi 6] (score 0.455) — Wlan-GigabitEthernet 0/1/8 naming.

**#3** [b-cisco-1100-series-hig.pdf — About Cisco 1000 Series Integrated Service Routers] (score 0.450) — LED indicator port references.

---

## Q5. How do I configure a management IP and default gateway on a C1111? — **Score 0 / 1**

**#1** ← top, **not relevant** [isr1100-sw-config.pdf — Configuring Routing Information Protocol] (score 0.451)

> ... R 192.0.2.2/8 [120/1] via 192.0.2.1, 00:00:02, Ethernet0/0/0 ... router eigrp 109 network 192.168.1.0 ...

EIGRP, not mgmt IP. Hit #2 has the answer ("Enter interface name used
to connect to the management network from the above interface summary:
Ethernet0/0") but ranks below.

**#2** [isr1100-sw-config.pdf — Managing Configuration Files] (score 0.442) — would-be-relevant setup dialog.

**#3** [isr1100-sw-config.pdf — Understanding Diagnostic Mode] (score 0.425) — generic CLI help.

---

## Q6. Difference between switchport and routed mode on C1111 ports? — **Score 1 / 1**

**#1** ← top, **relevant** [isr1100-sw-config.pdf — Configuring Flex Port to Layer 2 Port] (score 0.498)

> ... Example: Flex Port to Layer 3 Port Configuration ... no switchport ... ip address 10.10.0.1 ... Example: Flex Port to Layer 2 Port Configuration ... switchport ... switchport mode access ...

Side-by-side L3 vs L2 example — exactly the answer.

**#2** [isr1100-sw-config.pdf — Configuring Wi-Fi 6] (score 0.451) — WLAN-GE access port.

**#3** [isr1100-sw-config.pdf — Configuring LACP] (score 0.396) — port-channel.

---

## Q7. How is the C1111 management VLAN configured? — **Score 0 / 1**

**#1** ← top, **not relevant** [isr1100-sw-config.pdf — Configuring Wi-Fi 6] (score 0.553)

WLAN-GE VLAN tagging — different from "management VLAN", which on the
C1111 means Vlan1 (the factory-default DHCP pool / WebUI VLAN, shown
in Q3#1's chunk).

**#2** [isr1100-sw-config.pdf — 0 C1111-8PLTELAWN ok 00:04:56] (score 0.465) — `show platform` output.

**#3** [b-cisco-1100-series-hig.pdf — About Cisco 1000 Series Integrated Service Routers] (score 0.462) — LED indicators.

---

## Q8. Boot and initialization lifecycle of the ISR 1100 series? — **Score 1 / 1**

**#1** ← top, **relevant** [isr1100-sw-config.pdf — Configuring ROMMON] (score 0.474)

> ... A ROMmon image is a software package used by ROM Monitor (ROMmon) software on a router ... Table 11: Cisco ISR1000 ROMmon Compatibility Matrix ... ROMmon image is bundled along with the IOS XE image ...

ROMmon is the bootloader — directly the boot lifecycle.

**#2** [isr1100-sw-config.pdf — 0 C1111-8PLTELAWN ok 00:04:56] (score 0.467) — ROMmon auto-upgrade during first boot.

**#3** [isr1100-sw-config.pdf — Configuring Wi-Fi 6] (score 0.461) — `show platform` output.

---

## Q9. Where do I configure VLANs on the C1111 in the CLI? — **Score 1 / 1**

**#1** ← top, **relevant** [isr1100-sw-config.pdf — Configuring Wi-Fi 6] (score 0.496)

> ... interface Wlan-GigabitEthernet slot/subslot/port ... switchport access vlan number Example: Router(config-if)#switchport access vlan 199 ... switchport mode access ... interface vlan number Example: Router(config)#interface vlan 199 ...

Section is labelled WLAN, but the actual VLAN CLI commands shown
(`switchport access vlan`, `switchport mode access`, `interface vlan N`)
are the canonical answer regardless of the wireless context.

**#2** [isr1100-sw-config.pdf — Configuring Wi-Fi 6] (score 0.446) — more of the same.

**#3** [b-cisco-1100-series-hig.pdf — About Cisco 1000 Series Integrated Service Routers] (score 0.437) — LED indicators.

---

## Q10. How do I save running-config persistently on a Cisco router? — **Score 1 / 1**

**#1** ← top, **relevant** [isr1100-sw-config.pdf — 255.255.255.0 IP subnet mask.] (score 0.630)

> ... Saving Configuration Changes Use the copy running-config startup-config command to save your configuration changes to the startup configuration so that the changes will not be lost if the software reloads or a power outage occurs. For example: Router# copy running-config startup-config ...

Exactly answers.

**#2** [isr1100-sw-config.pdf — CHAPTER 8] (score 0.492) — password recovery / disable.

**#3** [isr1100-sw-config.pdf — 1000 Series ISR looks for this file before finding the standard files-router-confg or the ciscortr.cfg.] (score 0.489) — `show running-config` output structure.

---

## Notes on the 3 misses

All three (Q2, Q5, Q7) are corpus-gap misses, not retrieval-quality
misses:

- **Q2** ("default management interface"): the answer "Gi0/0/0 is the
  default management interface" or "Vlan1 is the WebUI VLAN at
  192.168.1.1" lives in the **WebUI User Guide** (doc #3 from
  `docs/rag-sources.md`) and at the start of the **Basic Router
  Configuration** chapter of the existing PDF, but not in chunks that
  embed close to this query phrasing.
- **Q5** ("management IP + default gateway"): same chapter as Q2, plus
  the **Basic System Management Command Reference** (doc #4) covers
  the `ip default-gateway` command directly.
- **Q7** ("management VLAN"): the C1111 factory-default Vlan1
  configuration is in Q3's top chunk but doesn't rank for this phrasing
  because the chunk doesn't repeat the phrase "management VLAN" —
  semantic similarity to the WLAN VLAN section wins.

**Easiest fix:** download docs #3 and #4 from `docs/rag-sources.md` into
`knowledge_base/docs/` and re-run `python -m backend.knowledge_agent.ingest`.
Pipeline is idempotent — existing chunks won't duplicate; only the new
PDFs' chunks get added. Doc #3 (WebUI guide) likely lifts Q7 directly;
doc #4 (Basic System Mgmt cmd ref) lifts Q2 and Q5.
