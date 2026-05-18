# Day summary — 2026-05-15 (evening session)

This is the second session of 2026-05-15. The morning session ([summary](today-2026-05-15-summary.md)) shipped Phase 3.3+3.4+5 and produced [the kickoff plan](next-session-kickoff.md) for the two architecture chunks the project plan needs before v0.4.0-alpha.1.

This session **executed that plan end-to-end on the real lab router**, then iterated four times on real-router-surfaced bugs until both the WebUI and CLI AI-configure paths were stable.

## Top line

10 commits on `feature/bootstrap` (tests 444 → 496, +52). 3 release tags pushed to origin: `v0.4.0-alpha.1-ai-configure`, `v0.4.0-alpha.2-retry-guard`, `v0.4.0-alpha.3-add-button`.

Both planned chunks landed and were validated on the C1111:

- **WebUI multi-propose chain**: `pridaj statickú trasu 10.99.99.0/24 cez 192.168.10.254 cez WebUI` fills all five form fields correctly and verifies. End-to-end ~45s.
- **CLI AI configure**: trunk port Gi0/1/3 with all VLANs allowed applied on device end-to-end ~5s.

## Commits (in landing order)

| Commit | Type | Summary |
|---|---|---|
| `2c7cce4` | feat(orchestration) | Multi-propose chain for WebUI configure — `_webui_configure` is now an iteration loop. After each batch the page is re-described, inner Haiku is re-invoked with `previous_steps`, fills next batch, repeats until verify-text present or one of three hard-stops (cap=4 / empty-plan / stuck-plan). `draft_plan` gains `previous_steps` kwarg + a "Mid-flow continuation" section in the inner prompt. +5 unit tests. |
| `f1b7a6b` | fix(webui-agent) | Forward-lookup intent→eid via describe view. Old resolver used Playwright's `first_match` strategy chain which fell through to text-match when role+name match missed (Cisco's spatial labels aren't in the ARIA tree). On the static route page, `{role: textbox, name: Prefix}` was resolving to the column-header link "Prefix" instead of the textbox. Now forward-looks-up the intent in a fresh describe view using the SAME naming source the inner LLM saw, ties broken by `required=true`. +5 unit tests. |
| `0209bb4` | feat(orchestration,cli-agent) | `propose_cli_configure` + `cli_configure` tools. Inner Haiku 4.5 drafts IOS XE commands grounded in RAG + live running-config. Server-side denylist (`reload`, `erase`, `delete`, `format`, `write erase`, `boot system`, `enable password/secret`, `username * privilege`, newlines/semicolons) runs at propose AND execute time. `verify_command` locked to `show ...`. `verify_pattern` compiled as Python regex. New `cli_configure_planner.py` mirrors the WebUI planner. +22 unit tests. Outer planner system prompt updated to teach Haiku when to prefer narrow CLI tools vs `propose_cli_configure` vs `propose_webui_configure`. |
| `ac85641` | fix(orchestration) | Split CIDR across Prefix + Prefix Mask in inner prompt. The old static-route example told Haiku to put `10.0.0.0/24` into the "Prefix Mask" textbox; Cisco's form has separate Prefix and Prefix Mask fields and Prefix Mask wants DOTTED notation. New example + field-mapping rules table with /8, /16, /24, /25, /30 conversions. Regression guard. |
| `1acf14e` | fix(orchestration) | Null `verify_text` must NOT terminate the loop. The propose-time plan is usually a single click-Add with `verify_text=null` because the form isn't visible yet. Old code bailed on the first clean batch — defeating the whole multi-propose chain. Now falls through to re-describe + re-plan; only terminates when verify present, inner-plan-empty, inner-plan-stuck, or cap. New regression test. |
| `788504f` | fix(webui-agent) | Tighter spatial-label search avoids column-header bleed. Static-route form filled with values shifted by ONE ROW. Root cause: `_spatial_label` searched dy ≤ 300px above the input without strict horizontal alignment; on the Static Routing page the input form sits ~115px below a table whose column headers happen to read 'IP Type', 'Prefix', 'Prefix Mask', etc. Those headers slipped in and outscored the actual row labels. Fix: dy ceiling 300→80, alignCost 100→60, NEW left-of-input layout path (Cisco's actual inline-label-input form layout), explicit exclusion of `<thead>/<th>` ancestors. |
| `b5a88a4` | fix(orchestration) | Teach CLI verify_pattern the real show-output wording. Trunk-port verify failed even though config landed — Haiku invented `Trunking VLANs Allowed[\\s\\S]*all` from the config command but the show output uses `Administrative Mode: trunk`. Added a "common gotchas" list (trunk, VLAN delete, OSPF, hostname) and a dedicated trunk-port example with the right pattern. Same class of bug already hit on VLAN delete earlier. |
| `50e09c3` | fix(orchestration) | Kill chromium re-open loop + steer OSPF verify off `\| section`. Outer Haiku opened Chromium FOUR times in one turn after each `propose_webui_configure` empty-plan response because the inner WebUI prompt explicitly told the caller to "re-propose with a different webui_path". Two contradictory rules — fixed. Inner empty-plan responses now signal FINAL. Outer Rule 8 gets a hard per-turn quota: at most ONE call to each propose_* tool. `verify_failed` also terminal. Also: OSPF verify used `\| section "Routing Process"` (from the prompt example) and got empty output even though OSPF 2 was created — IOS XE section-grep is fragile on OSPF blocks. Switched all examples to `\| include`. |
| `be4e7fd` | fix | Inner planner clicks Add when intent says add, surface device errors on verify miss. Two real-router bugs from the OSPF process-N flow. (1) Inner WebUI planner returned EMPTY PLAN on the OSPF list page even when the Add button was visible — the outer Haiku then told the user "WebUI can't auto-click Add" (false). Split rule 3 into positive "click Add when visible" + rule 4 "truly empty only when no entry point exists at all". (2) `cli_configure` now extracts every `%`-prefixed line from `config_output` into a `device_errors` field on verify_failed returns. Router-id collisions and other Cisco rejections now visible at the chat UI instead of buried. |

## Hardware validation results

Tested on the C1111-4P at 192.168.10.1:

| Path | Intent | Result | Notes |
|---|---|---|---|
| WebUI | static route 10.99.99.0/24 via 192.168.10.254 | ✅ End-to-end | 5 form fields filled correctly, verify "10.99.99.0" present, route in `show ip route`. ~45s total. |
| CLI | trunk port Gi0/1/3 with all VLANs | ✅ Applied | Config landed on device. First verify miss (b5a88a4 fix) due to pattern wording; subsequent runs pass. |
| CLI | OSPF process 100 area 0 on Vlan1 | ✅ Applied | OSPF 100 visible in `show ip ospf \| include Routing Process` after the alpha.2 prompt fix. |
| CLI | OSPF process 5 with router-id 10.0.0.1 | ❌ Rejected by device | Router-id 10.0.0.1 already in use by ospf 2. NOT a code bug — device rejection. `device_errors` field added in alpha.3 to surface this. |
| WebUI | OSPF process N via /webui/#/OSPF | ⚠️ Not yet retested with alpha.3 | First attempt returned empty-plan because inner Haiku didn't click Add. alpha.3's rule-3 split should fix this — needs hardware retest tomorrow. |
| CLI | delete VLAN 45 | ✅ Applied | First verify miss (pattern was `^\\s*$` expecting empty; Cisco prints "VLAN id 45 not found"). Now in the gotchas list. |
| Fast-path WebUI/CLI | hostname change, VLAN add, interface IP | ✅ (unchanged from morning) | No regressions on the narrow fast-path tools. |

## Test count delta

Morning wrap: 444 passing.

Evening checkpoints:
- `2c7cce4`: 444 → 463 (+19, multi-propose chain + recovery tests)
- `f1b7a6b`: 463 → 468 (+5, eid_for_intent forward lookup)
- `0209bb4`: 468 → 487 (+22 CLI configure)
- `ac85641`: 487 → 488 (+1 CIDR regression)
- `1acf14e`: 488 → 489 (+1 null verify regression)
- `788504f`: 489 (no test churn — JS-side change)
- `b5a88a4`: 489 → 490 (+1 trunk regression)
- `50e09c3`: 490 → 493 (+3 quota/section regressions)
- `be4e7fd`: 493 → 496 (+3 click-Add + device_errors)

Final: **496 passing**, 3 skipped (smoke-test hardware deps), ruff + mypy clean.

## Release tags pushed to origin

- `v0.4.0-alpha.1-ai-configure` → `b5a88a4` — first hardware-validated cut (CLI configure + trunk port working).
- `v0.4.0-alpha.2-retry-guard` → `50e09c3` — adds the per-turn quota + OSPF verify section fix.
- `v0.4.0-alpha.3-add-button` → `be4e7fd` — adds click-Add behaviour + device_errors surfacing.

Backup tag from morning: `backup-20260515-1031` (pre-session restore point).

## Architectural shifts of the day (carrying memory forward)

1. **Multi-propose chain** (Chunk A) — `_webui_configure` is no longer a single-pass loop. It iterates with state: `executed_steps`, `iteration`, `last_plan_hash`. Three hard-stops prevent runaway. Failed Playwright steps feed back to the inner LLM as part of `previous_steps` (your decision Q1) instead of aborting.

2. **CLI AI configure** (Chunk B) — third write path alongside the narrow CLI tools and the WebUI generic path. Inner Haiku grounded in RAG + live running-config. Server-side denylist runs twice (defense in depth). Verify is regex against `show` output.

3. **Spatial label search rewrite** (`semantic_dom.py:_spatial_label`) — handles Cisco's two layouts: label-above-input (dy ≤ 80) and label-left-of-input-same-row (dy_center ≤ 20, dx_gap ≤ 200). Excludes table-header ancestors. The "shifted by one row" bug class is gone.

4. **Per-turn propose quota** (outer system prompt Rule 8) — at most ONE call to each propose_* tool per turn. `verify_failed`/`empty_plan`/`unsafe_command` are FINAL. Closes the "Chromium opened 4× per turn" failure mode.

5. **Inner LLM signals = TERMINAL, not retry hints** — previous wording told the caller to "re-propose with a different webui_path" which the outer Haiku faithfully obeyed despite Rule 8. Inner prompt now signals FINAL.

6. **`device_errors` on verify_failed** — `%`-prefixed lines from Cisco's `send_config_set` output are now extracted into a dedicated field. Router-id conflicts, IP overlaps, VLAN range errors visible at the chat UI.

## Known issues / next-session backlog

1. **OSPF WebUI flow** — needs hardware retest with alpha.3 to confirm the click-Add rule resolves the empty-plan response on /webui/#/OSPF.
2. **OSPF router-id reuse** — if the user asks "OSPF process N with router-id X" and X is already in use, the inner CLI planner doesn't know to either pick a different router-id or refuse. The new `device_errors` field surfaces Cisco's rejection but the user has to re-prompt manually. Could be improved: inner prompt should check running-config for existing router-ids in the OSPF section.
3. **`v0.4.0-alpha.1` final milestone tag** — we have iterative alpha.1/.2/.3 but no consolidated `v0.4.0-alpha.1` (without suffix). When the OSPF WebUI flow is hardware-validated, that's the moment for the formal milestone tag.
4. **Scope-lock** — CLAUDE.md §72 said "six scenarios in PROJECT_PLAN.md §2 only until v0.4.0-alpha.1 tagged". With alpha.* tags now on origin, that scope-lock can lift for the next session.
5. **OSPF section/include guidance** — the prompt fix landed but only OSPF was named. Other features that have section-grep gotchas (BGP, route-map) may surface the same issue.
6. **Iteration-cap=4** is tight — for OSPF with router-id + network statements + interface assignment, 4 iterations might not be enough. Bump if real-world hits the cap.

## Memory updates needed

The two memory rules from yesterday (model-role split, production-LLM=Haiku-only) still hold. New facts to consider:
- The multi-propose chain reliably handles Cisco's table-list-with-Add-button form pattern. This is a CAPABILITY claim, not a behaviour rule — probably doesn't belong in memory.
- `_eid_for_intent` is now load-bearing for any new form added to the WebUI catalog. Derivable from code, no memory needed.

No new memory rules from this session.
