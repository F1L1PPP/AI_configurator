# Smoke Scenarios — the six that define alpha-1

These are the **only** scenarios the alpha freeze (`v0.4.0-alpha.1`, Day 9) has to
demonstrate. Nothing else is in scope before that tag. Each is wired as a runnable
script under `tests/smoke/` on Day 8; the harness in `scripts/run_smoke_tests.py`
executes all six 5× in a row at alpha freeze.

Every scenario records four pieces of evidence under `artifacts/`:
- **Snapshots:** `device-snapshots/<session>/{pre,post}.cfg` — `show running-config` before & after
- **Screenshots:** `screenshots/<session>/<step>.png` — Playwright pre/post for WebUI scenarios; CLI scenarios skip
- **Trace:** `traces/<session>.zip` — Playwright trace on failure; not generated on success
- **Report:** `reports/<session>.json` — structured execution log (prompt, tool, params, result, timings)

---

## 1. CLI read — show commands

**Prompt:** "Show me the interfaces, version, and current running configuration."

**Expected tool:** orchestrator picks `cli_show_ip_interface_brief`, then
`cli_show_version`, then `cli_show_running_config`. No HITL — all read-only.

**Expected verification:** each returned payload is parsed via TextFSM / Genie
(`use_textfsm=True` in Netmiko). Smoke asserts:
- `show_ip_interface_brief` returns ≥3 interface rows (GigabitEthernet0/0/0,
  GigabitEthernet0/0/1, Vlan1 minimum)
- `show_version` includes `version_short` and `hostname` fields
- `show_running_config` is non-empty and contains a `hostname` line

**Evidence:** `reports/<session>.json` with the three tool calls + their parsed
results. No screenshots, no snapshots (read-only).

---

## 2. CLI write — set hostname

**Prompt:** "Change the hostname to LAB-R1."

**Expected tool:** `cli_set_hostname(new_name="LAB-R1")`. Orchestrator pauses
on `awaiting_approval` WebSocket event. Smoke harness auto-approves via
`POST /api/approve/{action_id}`.

**Expected verification:** post-write the agent calls
`cli_show_running_config | i hostname` and asserts the output contains
`hostname LAB-R1`.

**Evidence:**
- `device-snapshots/<session>/pre.cfg` — running-config before
- `device-snapshots/<session>/post.cfg` — running-config after
- `reports/<session>.json` with the approval timestamp + verification result
- Rollback runs at end of smoke: `restore_config(pre.cfg)` returns the device to
  its prior state.

---

## 3. CLI write — set interface IP

**Prompt:** "Set the IP on GigabitEthernet0/0/1 to 10.99.99.1/24."

**Expected tool:** `cli_set_interface_ip(interface="Gi0/0/1", ip="10.99.99.1",
mask="255.255.255.0")`. HITL approval required, auto-approved by smoke.

**Expected verification:** `show ip interface brief` parsed via TextFSM, the
row for `Gi0/0/1` has `ip_address == "10.99.99.1"` and `status == "up"`.

**Evidence:** snapshots (pre/post), report, rollback at end of smoke.

---

## 4. RAG query — doc-grounded answer with citations

**Prompt:** "How do I add an access VLAN on a Cisco C1111 via the IOS XE 17.x
CLI?"

**Expected tool:** `search_docs(query=..., top_k=5)` returns 5 chunks from the
local ChromaDB. Orchestrator composes the reply using the chunks; the reply
includes a `Sources:` section listing file name + page or heading for each
chunk used.

**Expected verification:** smoke harness asserts:
- The reply text contains the words `vlan` and `interface`
- The reply contains a `Sources:` section
- The Sources section has ≥1 entry pointing to a file in `knowledge_base/docs/`
- Manual relevance check (run-once, recorded in `reports/`): hand-graded
  ≥7/10 on the 10-query evaluation set from Day 7

**Evidence:** `reports/<session>.json` with the query, retrieved chunks (with
distance scores), and the final reply.

---

## 5. WebUI write — change hostname

**Prompt:** "Change the hostname to LAB-R1, but use the WebUI this time, not the
CLI."

**Expected tool:** `webui_change_hostname(new_name="LAB-R1")`. Orchestrator
picks the WebUI variant because of the explicit phrasing. HITL approval gate,
auto-approved by smoke.

**Expected verification:** after the Playwright flow submits, the agent
falls back to **CLI** for verification — calls `cli_show_running_config | i
hostname`, asserts the new hostname is present. Cross-tool verify is the point:
WebUI did the write, CLI proves it landed.

**Evidence:**
- `screenshots/<session>/01-login.png` through `06-after-submit.png` — every
  Playwright click bracketed by a screenshot
- `device-snapshots/<session>/{pre,post}.cfg`
- `reports/<session>.json`
- `traces/<session>.zip` on failure only

---

## 6. WebUI write — add access VLAN

**Prompt:** "Add VLAN 30 named OFFICE as an access VLAN on GigabitEthernet0/0/1."

**Expected tool:** `webui_add_access_vlan(vlan_id=30, name="OFFICE",
interface="Gi0/0/1")`. HITL approval gate, auto-approved by smoke.

**Expected verification:** **two** verification calls:
- Via CLI: `show vlan brief` parsed, assert row with `vlan_id == 30` and
  `name == "OFFICE"` exists
- Via WebUI: after the form submits, navigate back to the VLAN list page,
  screenshot it, assert the new row is visible (DOM contains `<td>30</td>`
  near `<td>OFFICE</td>`)

**Evidence:** screenshots (all steps + final list view), snapshots, report,
trace on failure.

---

## What's NOT in scope before alpha freeze

OSPF, ACLs, DHCP, static routes, additional `show` commands beyond §1, any
multi-step orchestration that chains scenarios together, GUI polish beyond
the screens listed in the Day-1 — Day-8 GUI track. These all wait until after
`v0.4.0-alpha.1` is cut and `release/alpha-1-freeze` is the safe-rollback
floor.
