# Smoke findings — 2026-06-05 (live C1111-4P)

Living fix-backlog captured while smoking the **existing** driver during the Atlas
rebuild (feature/bootstrap; atlas A/B/C1/C2 committed but NOT yet wired into the
live path — C3/C4 pending). These are **fix-later** items, not blockers for the
atlas work. Most are CLI-agent / orchestration / frontend bugs that persist
regardless of the WebUI rebuild.

## Group A results

| # | Scenario | Result |
|---|---|---|
| A1 | CLI read (show int/version/run) | ✅ pass |
| A2 | CLI write hostname | ✅ pass |
| A3 | CLI write interface IP | ⚠️ pass-with-bugs (IP landed, but first try errored + slow) → F1–F5 |
| A4 | RAG query | ✅ pass |
| A5 | WebUI hostname | ✅ pass (smooth) |
| A6 | WebUI add VLAN | ✅ pass (smooth) |

Group B (OSPF / DHCP) = next; findings appended below.

---

## Findings

### F2 — `set_interface_ip` blindly prepends `no switchport`, breaks routed ports — **HIGH** ✅ root cause confirmed in code
- **Symptom:** `Set the IP on GigabitEthernet0/0/1 to 10.99.99.1/24` (`act_20260605_657b85`) → `Execute failed: execute_tool(set_interface_ip) -> tool_failed: device rejected one or more config commands: % Invalid input detected at '^' marker.` The IP **actually landed** anyway (IOS applies config line-by-line and continues past the rejected line).
- **Root cause:** [write_tools.py:471](backend/cli_agent/write_tools.py#L471) — `set_interface_ip` unconditionally inserts `" no switchport"` (see docstring L455–458). On the C1111-4P, **Gi0/0/0 and Gi0/0/1 are routed WAN ports that don't support `switchport` at all**, so `no switchport` itself throws `% Invalid input detected at '^' marker`. Netmiko sees the `%` error → `tool_failed` → action → FAILED, even though the subsequent `ip address` line applied. (The docstring only anticipated the Gi0/1/x switchports, not the routed Gi0/0/x case.)
- **Fix (later):** detect switchport capability before deciding — only prepend `no switchport` when the port is actually L2 (there's a hint at [read_tools.py:110](backend/cli_agent/read_tools.py#L110) about checking switchport status first). Alternatively, treat a `no switchport`-line-only `% Invalid input` as benign (the port is already routed) instead of failing the whole action. Add a regression covering both a routed Gi0/0/x and a switched Gi0/1/x.

### F3 — Sticky bar lets Approve/Execute fire on a FAILED action — **MEDIUM** ✅ confirmed
- **Symptom:** after the F2 execute failure, retries produced: `Approve failed: action_id ... is in state 'FAILED'; expected one of ['PROPOSED']` and `Execute failed: ... in state 'FAILED'; expected ['APPROVED']`.
- **Root cause:** FAILED is terminal in the state machine ([confirmations.py:57](backend/orchestration/confirmations.py#L57) guard). The **frontend sticky bar kept the Approve/Execute buttons live** on a dead action_id, so clicking them hits the guard and errors.
- **Fix (later):** on an execute failure, disable Approve/Execute for that action_id in the sticky bar and surface a "propose a new fix" affordance instead. A dead action should never offer its old buttons.

### F4 — Diagnose→re-propose: description ≠ commands, dropped the IP step — **MEDIUM** (hypothesis)
- **Symptom:** the re-proposal `act_20260605_bce00e` was titled "Configure GigabitEthernet0/0/1: **remove shutdown and assign IP 10.99.99.1/24**" but its IOS XE commands were only `interface Gi0/0/1` / `no shutdown` / `exit` — **no `ip address` line**. Description over-promised; the IP-assignment command was dropped from the re-plan (it had already landed via F2, but the planner didn't know that).
- **Where to look:** the diagnose / re-propose path (generic `cli_configure` inner planner — `configure_planner.py` / `cli_configure_planner`). Description text and the emitted command list diverged.
- **Fix (later):** keep the proposal description consistent with the actual command block (derive the description FROM the commands, or validate they agree before surfacing). Decide whether the re-plan should re-include the IP command or detect it's already present.

### F5 — Proposal preview hides the real command block (`no switchport` not shown) — **LOW/MED** (known QUAL-1/SEC-F)
- **Symptom:** the `act_20260605_657b85` preview showed only `interface Gi0/0/1` / `ip address ...` — the `no switchport` line the tool actually sends (and that failed) was invisible, making the error hard to understand.
- **Root cause:** the live stream / preview is **intent, not wire-confirmation** — `_emit_cli_commands` fires before the SSH send and doesn't reflect tool-injected lines (already logged as QUAL-1/SEC-F in `docs/next-session-kickoff.md`).
- **Fix (later):** show the exact command block that will be sent (including tool-injected `no switchport`), or clearly label the preview as "intent."

### F1 — Interface-IP path is slow — **MEDIUM** (perf)
- **Symptom:** A3 noticeably slower than A2; the CLI write felt heavy even on success.
- **Where to look:** post-write verification SSH round-trip — `_verify_running_config` does an **unconditional** SSH read per write (already logged as **QUAL-2** in `docs/next-session-kickoff.md`: "skip when the netmiko-output check was clean, or shorten the success-path read timeout 60s→5s"). Plus possible Netmiko reconnect latency after idle.
- **Fix (later):** land QUAL-2 — skip or shorten the post-write verify read when the config-set return was already clean. Profile the Netmiko send + verify split to confirm where the seconds go.

---

## Group B findings (OSPF / DHCP)

### B1 — OSPF basic page
- **Result:** ✅ the *Basic* OSPF page works on the **existing** driver. Prompt `Using the WebUI, configure OSPF process 100 with router id 10.10.10.1` (`act_20260605_28a6ff`) → clean plan (fill Process ID=100, fill Router ID=10.10.10.1, click Apply) → Executed. Basic OSPF = two plain textboxes, **no Kendo dropdown**, so the existing driver handles it. (Confirms the Kendo dropdown is the specific blocker, not plain forms.)
- **My smoke prompt was wrong:** `configure OSPF process 100 in area 0` — **area is NOT on the Basic page**; Area/Network/Wildcard are under **Advanced**. Corrected the checklist.
- **Finding → feature (FEAT-SMART below):** when asked for `area`, the agent should NOT silently do a partial Basic config or flatly say "not in documentation." It should recognize area lives on Advanced, explain the tradeoff (Advanced also needs Network + Wildcard), and offer: switch to Advanced (gather the extra inputs) / do Basic without area / use CLI.

---

### B2 — DHCP pool (Kendo Subnet Mask) — ✅ worked, but slow + a mis-placement bug
- **Result:** ✅ pool MYPOOL created on the **existing** driver (`act_20260605_656180`). **Subnet Mask Kendo dropdown resolved correctly** (255.255.255.0). The agent **self-debugged** a corrupted Starting ip (`20.20.20.120.20.20.2` → `20.20.20.2`, visible across the two screenshots) — good recovery behavior.
- **B2a — value mis-placement (MED):** the requested **default gateway 20.20.20.1 has no field on the Basic DHCP form** (Default Router lives on Advanced). The agent forced it into **Starting ip** (`20.20.20.1` concatenated with `20.20.20.2`) before correcting. Two faults: (1) a non-mappable value was crammed into the wrong field instead of being flagged as a gap; (2) the fill concatenated rather than replaced. Same capability-gap class as OSPF area → covered by FEAT-SMART + per-field read-back (SPEED/C3).
- **Confirms:** Kendo dropdowns are drivable; the blocker is SPEED + capability-awareness, not the widget itself.

### B2b — `inner_plan_empty` at EXECUTE (re-plan-at-execute fragility) — **HIGH** ✅ confirmed in code; eliminated by C4
- **Symptom (2026-06-05 re-run, `act_20260605_2a17f0`):** a GOOD proposal (7 steps incl. `verify combobox "IP Type"=IPV4`, `verify combobox "Subnet Mask"=255.255.255.0`) but **Execute failed:** `execute_tool(webui_configure) -> inner_plan_empty`. **Non-deterministic** — a prior identical run filled the form (slowly) and succeeded.
- **Root cause:** `_webui_configure` (execute) RE-PLANS via the inner LLM at execute time — `draft_plan(..., previous_steps=...)` ([tool_registry.py:1949](backend/orchestration/tool_registry.py#L1949)), the "multi-propose continuation" — and when that re-plan returns empty it errors `inner_plan_empty` (~L2001-2007). **The approved plan is NOT what runs**; execute re-invokes a non-deterministic LLM that can return empty (form not re-opened, or it judges "nothing to do").
- **Fix = C4:** the atlas path plans ONCE (deterministic `perceive` → `draft_atlas_plan` → `validate_atlas_plan`), stores the validated field plan on the action, and at execute runs EXACTLY that via `act_field`/`apply_control` — **no LLM re-plan at execute**. `inner_plan_empty` becomes structurally impossible; "approve = what runs."

---

## SPEED — the #1 pain, fixed generally by the atlas act path (C3/C4)
- **Evidence:** DHCP took **>5 min**; ~**1 min** of idle between "form filled" and clicking Apply.
- **Root cause (existing driver, per step):** re-`describe_page` = dozens of per-element Playwright round-trips (200 ms probes each); `_settle_page` waits on `networkidle` (always times out → 800 ms) + 250 ms fallback; proactive plan-vision Haiku call (5–15 s); plus any poisoned selector-cache stalls.
- **Fix — GENERAL, every page (this is C3/C4):** ONE `accessibility.snapshot()` per perceive (~50 ms, zero round-trips); explicit `expect(apply_control).to_be_visible()` instead of `networkidle`; vision demoted off the hot path. The ~1-min-to-Apply collapses to ~1 s. Not page-specific — applies to OSPF, DHCP, and every other page identically.

---

## General-configuration principles (Director directive, 2026-06-05)
Every fix below must be **GENERAL** — driven by the per-page atlas + a generic policy, handled the SAME on every WebUI settings page. **No per-page or per-widget-type hardcoding.**
- **Speed** → atlas act path (C3/C4). All pages.
- **Capability-awareness + Advanced discovery/suggestion** → FEAT-SMART (atlas tabs + resolver + clarify + suggest). All pages.
- **Per-field READ-BACK verify** (C3) + **only fill fields that map** → catches the Starting-ip corruption AND the gateway mis-placement generally (never cram an unmapped value into the wrong field; surface it as a gap instead).

---

## FEAT-SMART — capability-aware planning + clarification (atlas-driven, generalized)

**Problem:** the agent treats "the current page/form" as the whole world. If a requested field isn't on it, it either silently drops it (partial config) or refuses ("not in documentation"). It should instead reason about WHERE a capability lives, surface the decision, and navigate there — on EVERY page, not hardcoded. ("Claude-in-Chrome" decision-making + path-finding + self-debug.)

**Why the atlas makes this natural:** the atlas already records, per page, which fields exist. Extend it to tabs/variants and add a cross-atlas search + a decision policy.

Building blocks (Phase G — AFTER the core atlas driver C3/C4 is green):
1. **Atlas captures tabs/variants.** Detect `role="tab"` (Kendo Basic/Advanced tabs) during capture; record `tabs: [{name, activate_locator, fields:[...]}]` per route (crawler clicks each tab read-only to enumerate its fields). So the atlas knows OSPF Basic = {process_id, router_id}; OSPF Advanced = {process_id, network, wildcard, area, …}.
2. **Capability resolver (concept → location).** Given a requested concept the current view can't satisfy (e.g. "area"), search ALL captured routes+tabs for a field matching it (normalized label + a small synonym map). Returns (route, tab, field) or "not in WebUI".
3. **Gap detection.** Reuse C2's `validate_atlas_plan` `unknown_field_key` signal + have the planner explicitly list "requested-but-unavailable-here" concepts. Run the resolver on them. Three outcomes:
   - lives on another tab/page → **clarification** (not a silent partial plan);
   - lives nowhere in the WebUI atlas → "not WebUI-configurable; do it via CLI?" (atlas-backed, replaces the vague "not in documentation");
   - all available here → proceed.
4. **Clarification turn (new chat affordance).** Before proposing, the agent asks: "Area isn't on Basic OSPF — it's under Advanced, which also needs Network + Wildcard. (1) Switch to Advanced (I'll ask for those), (2) Basic without area, (3) CLI?" On the user's choice it proceeds.
5. **Self-navigation / path-finding.** Use the atlas nav graph (nav_click_path + tab activate controls) to deterministically reach the chosen tab/page (capture-on-visit if not yet mapped). Then gather any missing required inputs (network/wildcard) via another short clarification, then plan the fill.
6. **Generalized, not hardcoded.** All page/tab/field knowledge is auto-captured per device; the gap→clarify→navigate→gather→plan policy is generic. New device or new settings page → same logic once the atlas captures it.

7. **Proactive Advanced discovery + suggestion.** When a page's atlas has an Advanced tab/section, the agent tells the user it exists and SUGGESTS what could be added there, sourced from the captured Advanced fields — e.g. after the Basic DHCP pool: *"Done. The Advanced section also offers Default Router (gateway), DNS server, Lease time, and DHCP options — want to set any?"* Generalized: any page whose atlas captured an Advanced variant surfaces its extra capabilities as suggestions. This also catches the B2a gateway case — instead of cramming gateway into Starting ip, the agent says "gateway isn't on Basic; it's the Default Router on Advanced — add it?"

**Depends on:** C3/C4 (working single-page atlas act path) first. Then FEAT-SMART = tab capture (extends B/E) + resolver (new, small) + clarification/suggestion turn (orchestration + reuse the proposal/HITL channel) + atlas navigation.

---

## Group C findings (2026-06-06 — breadth smokes: the generic atlas engine on NEW sections)

### C1 — Static route — ✅ GREEN (after a page-agnostic radio fix)
- **Prompt:** `add a static route to 10.50.0.0/24 via 192.168.10.254`. First attempt (`act_20260606_c79eb5`) false-failed on the **"IP Type" radio** with `verify_mismatch`: a radio used as a value-selector ("IP Type"=IPv4) was checked as a boolean, so the option label was run through the truthy-token test and a correctly-selected radio (the IPv4 default) was rejected → step_failed → never clicked Apply.
- **Fix (commit `429bc27`, page-agnostic):** a radio's "set" state = the targeted radio is checked — in BOTH the read-back verify and `_value_already_set` idempotent-skip. +3 regression tests. Re-smoke (`act_20260606_dd95e5`) **GREEN** — route configured end-to-end through the one generic engine. Proves a brand-new section needs no per-section code.

### C2 — Standard ACL form — ❌ Kendo dropdowns `element_intercepted` — **DEFER (HIGH effort, Director call 2026-06-06)**
- **Prompt:** `create a standard ACL named LAB-ACL that permits 10.0.0.0/8` (`act_20260606_63e50ccb`).
- **Result:** `step_failed: element_intercepted`. The **textboxes filled fine** (`aclname`=LAB-ACL ✓, `sequence`=10 ✓) but **both Kendo dropdowns failed**: `acl-name` (ACL type = "IPv4 Standard") and `source-type` (= "Network") → `element_intercepted` (Playwright timeout after 2 attempts — a popup/overlay covering the control).
- **Why it's the worst case for the current driver:** (1) multiple stacked Kendo dropdowns (the open→click fragility); (2) a **dependent/dynamic form** — choosing Source Type=Network reveals the source-IP + wildcard fields that don't exist in the page until then; (3) a same-label collision — the name textbox and the type listbox are both "ACL Name" (captured as `aclname` vs `acl-name`).
- **Depends on (not per-section):** the **Kendo WRITE path** hardening (prefer hidden `<select>` / scope popup by `aria-controls` — kills the `element_intercepted`), plus **dependent-field re-perceive** (re-capture after a select reveals new fields), plus **same-label disambiguation**. All three are general engine gaps already on the roadmap.
- **For now:** ACLs are CLI-only. Re-smoke ACL after the Kendo write-path fix lands.

### C3 — DHCP pool, NON-default subnet mask (Kendo write path) — ✅ GREEN (2026-06-06)
- **Prompt:** `via web ui add a DHCP pool named LABPOOL for network 172.16.50.0 with subnet mask 255.255.255.128` (`act_20260606_66d275caec7a44898b9ad50c3ffc35c8`).
- **Result:** ✅ end-to-end. `fill DHCP Pool Name=LABPOOL` · `fill Network=172.16.50.0` · **`select Subnet Mask=255.255.255.128` (/25, the non-default Kendo combobox)** · `click Apply to Device` → Approved → Executed, snapshot captured. This is the first time the **non-default Kendo dropdown write** has gone green live (prior /24 only passed via the idempotent-skip; see `docs/today-2026-06-06-summary.md` §3b).
- **Root-cause fix (page-agnostic):** `pickActiveSelect` in `backend/webui_agent/atlas/adapters.py` walked container visibility starting **at the `<select>` itself**, which Kendo always renders `display:none` → every candidate rejected on iteration 0 → fell through to the interception-prone popup click. Fix: start the walk at `select.parentElement` so it tests the **ancestor container** (the Basic/template copy lives in an `ng-hide`/`display:none` section; the active copy is rendered). ~1 line. Container-visibility (Branch 2) alone disambiguated the two same-name selects — the dead `window.jQuery` Branch 1 was not needed.
- **Unblocks C2 (ACL):** the `element_intercepted` on the ACL `acl-name` / `source-type` dropdowns is the same Kendo write path. Re-smoke ACL next (still also needs dependent-field re-perceive + same-label disambiguation per C2).

### C3b — DHCP pool /26 re-confirm + a Cisco-SPA bootstrap flake (2026-06-06)
- **Prompt:** `Using the WebUI, add a DHCP pool named LABP26 for network 172.16.80.0 with subnet mask 255.255.255.192` (`act_20260606_19293922b87248ddb48bec095d6711b9`).
- **Result:** ✅ GREEN. Running-config (post snapshot) shows `ip dhcp pool LABP26 / network 172.16.80.0 255.255.255.192` — the real-config backstop, not just the app's verify. The picker resolved `candidate_count=2` via `hidden_select` and chose `select_id="subnetmaskOptions"` (a *different* same-name copy than the /25 run's `subnetmaskOptionsDHCP`) yet wrote the correct value — the two duplicate selects share the Angular `ng-model`, so neither is a "wrong" copy that submits a different value.
- **First attempt false-failed on a transient SPA bootstrap flake (page-agnostic fix landed):** the Cisco WebUI AngularJS app loaded with controllers/services unbound (`browser_pageerror "reading 'controller'/'service'"`) → `perceive field_count=0` → the Add-button open-form click timed out (`click_timeout_unsafe_retry`) → empty plan → the orchestrator mis-advised a CLI fallback. **Fix:** bounded reload-and-retry around the read-only form-open click (`webui_reload_for_planning` re-bootstraps the SPA via `page.goto`; up to `_OPEN_FORM_MAX_ATTEMPTS=3`). +2 regression tests. The re-run loaded clean (no reload needed) and configured end-to-end.

### C4 — Standard ACL re-smoke: C2 dropdown blocker UNBLOCKED, dependent-field gap isolated — ⚠️ PARTIAL (2026-06-06)
- **Prompt:** `create a standard ACL named LAB-STD that permits 10.0.0.0/8` (`act_20260606_2db7385429794424abf386bdd3036f55`).
- **✅ The C2 Kendo dropdown blocker is GONE.** Both `aclTypeDropDown` ("IPv4 Standard") and `aclSourceType` ("Network") set via `kendo_select_success` (`hidden_select`, `candidate_count=1`) — **no `element_intercepted`**. The picker/hidden-select fix resolves them; this closes the dropdown half of C2 (deferred HIGH-effort on 2026-06-06).
- **❌ The ACL landed EMPTY** in the post running-config: `ip access-list standard LAB-STD` with NO permit entry. Selecting Source Type="Network" **reveals** the source-IP + wildcard fields, which did not exist when `draft_atlas_plan` ran (plan-once), so they were never filled — the **dependent-field re-perceive gap** predicted in C2.
- **Next fix (page-agnostic, the isolated C2 remainder):** a propose-time **dependent-form probe** — when a perceived dropdown's selection reveals new fields, set it during propose, re-perceive, and re-plan against the expanded form so the revealed fields enter the plan. Until then, ACLs with Network/Host source/dest are created empty (only a "permit any" standard ACL, which reveals nothing, would be complete). Related: FEAT-SMART Advanced-tab discovery.
- **Cleanup:** delete the empty `LAB-STD` on the router.
