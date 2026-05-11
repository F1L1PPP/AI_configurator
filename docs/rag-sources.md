# RAG Source Documents — Day 7 ingest plan

> The shortlist of Cisco documentation to embed into ChromaDB on Day 7. Each
> entry has the doc title, where to find it on cisco.com, the sections we
> actually need, and an estimate of how much of it ends up in the vector store
> after heading-aware chunking.
>
> **Hard rule per `PROJECT_PLAN.md §7 Day 7`:** total corpus ~50–100 MB. More
> than that and retrieval quality degrades (noisy chunks crowd out the relevant
> ones in the top-5 results).
>
> **Search workflow:** open the URL pattern below, search the page for the
> exact doc title, click through. Cisco reorganizes URLs every few quarters —
> the title + the search terms below are more stable than the direct links.

---

## What we're scoping for

The corpus only needs to answer questions for the six §2 scenarios:

1. **CLI read** — `show interfaces`, `show version`, `show running-config`, `show vlan brief` syntax + output structure
2. **CLI write — hostname** — `hostname` command syntax, where it persists
3. **CLI write — interface IP** — `interface Gi0/0/1`, `ip address X Y` syntax
4. **RAG query** — itself; this doc set is what answers it
5. **WebUI write — hostname** — Configuration → Administration → Device Properties → Hostname field
6. **WebUI write — Access VLAN** — Configuration → VLANs → Add VLAN form

Everything else (OSPF, ACLs, DHCP, BGP, AAA, IPsec, NAT, QoS, EEM, …) **stays out**. Adding it pollutes retrieval without unlocking any grading.

---

## The shortlist (priority order)

### 1. Cisco 1000 Series Integrated Services Routers Software Configuration Guide, Cisco IOS XE 17.x

- **Why we want it:** the canonical, platform-specific config guide for the C1111. Covers the device defaults, supported interfaces (Gi0/0/0–Gi0/0/3, switchport vs routed mode), the management VLAN, and the boot/init lifecycle.
- **Find it on cisco.com:** search `"Cisco 1000 Series Software Configuration Guide" "IOS XE 17"`. Lands in [`/c/en/us/td/docs/routers/access/1100/`](https://www.cisco.com/c/en/us/td/docs/routers/access/1100/).
- **Sections we actually need (the rest gets stripped on ingest):**
  - "Hardware Overview" — interface naming, port labels
  - "Basic Router Configuration" — hostname, management IP, default gateway
  - "Configuring VLANs" if present in the platform-specific guide
  - "Web User Interface" intro section — confirms WebUI is enabled by default
- **Estimated MB after stripping:** ~3 MB

### 2. Software Configuration Guide for Cisco IOS XE Software, Release 17.x — Layer 2 / LAN Switching

- **Why we want it:** the generic IOS XE config guide for Layer 2 — VLAN add/remove, access vs trunk mode, interface assignment to a VLAN. Less platform-specific than #1 but covers the deeper CLI syntax for VLAN management.
- **Find it on cisco.com:** search `"Cisco IOS XE Bengaluru 17.6" "Layer 2 Configuration Guide"` (substitute the actual 17.x release you have — confirm with `show version` on the C1111). Path is usually [`/c/en/us/td/docs/ios-xml/ios/lanswitch/configuration/xe-17/`](https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/lanswitch/configuration/xe-17/).
- **Sections we actually need:**
  - "Configuring VLANs" (the chapter, not the whole book)
  - "Configuring VLAN Trunks" — for completeness in case the agent is asked about trunk mode
  - "Layer 2 Interface Modes" — access vs trunk semantics
- **Estimated MB:** ~5 MB

### 3. Cisco IOS XE Web User Interface (WebUI) User Guide, Release 17.x

- **Why we want it:** the single most important doc for the WebUI agent. Names every screen, every nav path, every form field. Without this the RAG can't answer "how do I add a VLAN via the WebUI" with a precise step list.
- **Find it on cisco.com:** search `"Cisco IOS XE Web UI" "User Guide" 17`. Or jump from the C1111 product page → "Configure" → "Web UI Guide".
- **Sections we actually need:**
  - "Getting Started" / "Logging In" — confirms the URL pattern, the default landing page
  - "Configuration" menu reference — the top-level nav we'll be clicking
  - "VLANs" page reference — field names, valid ranges, error messages
  - "Administration → Device Properties" — where hostname lives
  - "Troubleshooting" — common WebUI errors we might see in screenshots
- **Estimated MB:** ~4 MB
- **Pin the exact version that matches the C1111's running WebUI.** Record the WebUI version in `docs/router-prerequisites.md` during the cabled session and use the matching doc.

### 4. Cisco IOS XE 17.x Command Reference — Basic System Management

- **Why we want it:** authoritative syntax for the CLI commands the agent uses. Specifically: `hostname <name>`, `show version`, `show running-config`, `show ip interface brief`, `show vlan brief`.
- **Find it on cisco.com:** search `"Cisco IOS XE Command Reference" "Basic System Management" 17`. Path usually [`/c/en/us/td/docs/ios-xml/ios/fundamentals/command/`](https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/fundamentals/command/).
- **Sections we actually need:**
  - `hostname` command reference page (exact syntax, persistence behavior)
  - `show running-config` command reference (output format)
- **Estimated MB:** ~2 MB (heavily filtered — we only keep the command pages we use)

### 5. Cisco IOS XE 17.x Command Reference — Interface and Hardware Component

- **Why we want it:** authoritative syntax for `interface GigabitEthernet0/0/X`, `ip address <ip> <mask>`, `no shutdown`. The interface IP write scenario depends on getting these right.
- **Find it on cisco.com:** search `"Cisco IOS XE Command Reference" "Interface and Hardware Component" 17`.
- **Sections we actually need:**
  - `interface` command (chapter intro + GigabitEthernet subcommand)
  - `ip address` command (regular form, not the secondary/dhcp variants)
  - `shutdown` / `no shutdown`
- **Estimated MB:** ~2 MB

### 6. Cisco IOS XE 17.x Command Reference — Switching (VLAN commands)

- **Why we want it:** the CLI verification side of the WebUI VLAN scenario. The agent runs `show vlan brief` to verify the WebUI add worked; RAG needs to know the expected output structure.
- **Find it on cisco.com:** search `"Cisco IOS XE Command Reference" "LAN Switching" 17`.
- **Sections we actually need:**
  - `vlan <id>` command
  - `switchport access vlan <id>` command
  - `show vlan brief` command (especially the output format — TextFSM parsing depends on it)
- **Estimated MB:** ~2 MB

### 7. (optional, only if size permits) Cisco 1000 Series ISR Data Sheet

- **Why:** physical port labels, supported interface types, model variants. Useful when the user asks "is this command supported on my hardware?"
- **Find it on cisco.com:** search `"Cisco 1100 Series Integrated Services Routers Data Sheet"`.
- **Sections needed:** all (it's a short doc)
- **Estimated MB:** ~1 MB

---

## Estimated total

**Required (#1–6):** ~18 MB raw → ~10 MB after stripping non-target sections.
**With optional (#7):** ~19 MB → ~11 MB.

Well under the 50 MB ceiling. **Don't be tempted to add more docs to fill the budget** — sparse, on-target corpus beats dense + noisy every time for top-5 retrieval. If we have 50 MB headroom, use it to add IOS XE release notes for the exact version (helps the agent answer "what changed between 17.6 and 17.9" if asked).

---

## On Day 7 — the ingest workflow

1. Download each PDF / HTML page from the search above into `knowledge_base/docs/` (gitignored).
2. Use a heading-aware chunker (~500 tok, 50-tok overlap, never split mid-table).
3. Embed each chunk via `sentence-transformers/all-MiniLM-L6-v2` (384-dim, local, no API key).
4. Persist to ChromaDB at `knowledge_base/vectorstore/` (also gitignored).
5. Run the 10-query relevance test (see plan §7 Day 7 + `docs/smoke-scenarios.md` scenario 4). Pass = ≥7/10 returns a chunk whose source matches a hand-graded answer.

---

## Why this list, not the obvious "all of Cisco's docs"

Scoping discipline. The retrieval quality of a 100-doc corpus with mostly-irrelevant content is *worse* than a 5-doc corpus with all-relevant content, because `top_k=5` will return at least one off-topic chunk that pollutes the LLM's context window. With this curated list every chunk that surfaces is from a doc that's directly answering a §2 scenario question.

---

## Verify URLs before downloading

Cisco shuffles its documentation tree every few quarters. Before Day 7, open
each URL above; if it 404s, search for the exact doc title in
[cisco.com/c/en/us/support/](https://www.cisco.com/c/en/us/support/) and
update this file with the working URL + the date you verified it. Update the
record below as you go:

| # | Doc | URL verified working as of | Local path after download |
|---|---|---|---|
| 1 | C1111 Software Config Guide IOS XE 17.x | — | `knowledge_base/docs/01_c1111_software_config.pdf` |
| 2 | IOS XE 17.x Layer 2 Config Guide | — | `knowledge_base/docs/02_iosxe_l2_config.pdf` |
| 3 | IOS XE WebUI User Guide 17.x | — | `knowledge_base/docs/03_iosxe_webui_user_guide.pdf` |
| 4 | IOS XE 17.x Command Ref — Basic System Mgmt | — | `knowledge_base/docs/04_cmdref_basic_system.pdf` |
| 5 | IOS XE 17.x Command Ref — Interface | — | `knowledge_base/docs/05_cmdref_interface.pdf` |
| 6 | IOS XE 17.x Command Ref — LAN Switching | — | `knowledge_base/docs/06_cmdref_lan_switching.pdf` |
| 7 | (optional) C1100 Series Data Sheet | — | `knowledge_base/docs/07_c1100_data_sheet.pdf` |
