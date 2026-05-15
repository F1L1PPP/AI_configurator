# Next session kickoff — 2026-05-16+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. Read these three docs first, then summarise back in 4-6 sentences:

1. [docs/today-2026-05-15-summary.md](docs/today-2026-05-15-summary.md) — yesterday's wrap (12 commits, Phase 3.3+3.4+5+runaway-fixes-and-Haiku-swap)
2. [docs/plan-ai-first-webui.md](docs/plan-ai-first-webui.md) — the v0.4.0 phase plan
3. [docs/security-review-2026-05-14.md](docs/security-review-2026-05-14.md) — the open hardening backlog

After reading, summarise:
1. The highest-impact remaining security findings (cross-reference today's 8-layer defense in depth)
2. What Phase 5 does + the known multi-step gap
3. Today's first chunk of work + the parallel CLI track
4. What you'll touch first and what you'll NOT touch

Then wait for "go" before making any change. Don't propose re-planning — the plan is locked unless I ask.

## Today's first chunk — multi-propose chain for general WebUI config (~3 hrs)

**Why**: Phase 5 today produces single-step plans when the intent requires a page transition (e.g. "add static route" needs click Add → form opens → fill fields → click Apply). Inner Haiku only sees one view at propose time. This blocks every multi-page Cisco form.

**Architecture**: after `webui_configure` executes a step, server re-describes the page and re-invokes the inner planner if the verify text hasn't appeared yet AND iteration cap not hit.

**File plan**:
- `backend/orchestration/tool_registry.py` — modify `_webui_configure` to loop: after each step, call `webui_describe_page`, then call `draft_plan(intent, rag_chunks, fresh_view)` to get remaining-steps plan, then execute those steps. Continue until `webui_verify(verify_text)` returns `present=True` OR iteration cap (suggest 4) reached.
- `backend/orchestration/configure_planner.py` — `draft_plan` already takes a view + intent; reuse as-is. Inner Haiku's SYSTEM_PROMPT (`_INNER_SYSTEM_PROMPT`) already says "if target not visible, return empty plan with risk note" — multi-propose chain interprets empty-plan-with-risk as "continue iteration after a step"; non-empty plan executes next.
- New behavior: track `previous_steps` across iterations so the inner Haiku can be told "you already did X, Y, Z — now what?".

**Constraints**:
- Same `action_id` across all iterations (preserves Threat 1 closure)
- Cap iterations at 4 (defensive; OSPF + RIP forms shouldn't need more)
- Each step still goes through `webui_act_by_intent` (Phase 3.4 spatial labels in the describe view; QW3 deny-list still enforced per step)
- `mark_executed` only on final-iteration success (after `webui_verify` returns present)

**Tests**:
- Unit: mock `webui_act_by_intent` + `webui_describe_page` + `draft_plan` to simulate a 3-step OSPF flow. Assert iterations run in order, `mark_executed` fires once at end.
- Real-router: ask agent `pridaj statickú trasu 10.99.99.0/24 cez 192.168.10.254 cez WebUI`. Expected flow:
  - Iteration 0: click Add (visible in pre-click view)
  - Iteration 1: fill Prefix / Prefix Mask / Next Hop (now visible in post-click view with Phase 3.4 labels), click Apply to Device
  - verify "10.99.99.0" present → mark_executed

## Second chunk — CLI + AI configuration (~2 hrs)

**Why**: WebUI doesn't expose everything in IOS XE. Many features (BGP, route-maps, complex ACLs, debug commands) are CLI-only. The same propose/execute + RAG + HITL pattern works for CLI but is simpler — no browser, no DOM resolution, no spatial labels.

**New tools**:
- `propose_cli_configure(intent: str)` — outer Haiku-callable
- `cli_configure(action_id: str)` — outer Haiku-callable, requires APPROVED action_id

**`propose_cli_configure` flow**:
1. Validate intent
2. `search_docs(intent, top_k=3)` — RAG grounding for IOS XE command syntax
3. Take pre-snapshot via existing `cli_agent/snapshots.py:take_snapshot(action_id, "pre")` — captures running-config
4. Inner Haiku drafts an IOS XE config command list from (intent + RAG chunks + current running-config). Output JSON shape:
   ```json
   {
     "config_commands": ["router ospf 100", "network 10.0.0.0 0.255.255.255 area 0", "exit"],
     "verify_command": "show ip ospf | include 100",
     "verify_pattern": "Routing Process \"ospf 100\"",
     "risk": "Adds OSPF process 100 to the running-config; can be removed via 'no router ospf 100'."
   }
   ```
5. `propose_action(tool="cli_configure", params={"intent": ..., "config_commands": ..., ...})` → action_id
6. Return `awaiting_approval` with the plan preview

**`cli_configure` flow**:
1. `is_approved(action_id)` check
2. Retrieve stored config_commands + verify_command + verify_pattern from action dict
3. Send config block via `netmiko.send_config_set(config_commands)` (existing pattern in `cli_agent/write_tools.py`)
4. Run verify_command via `show_running_config` + regex against verify_pattern
5. On verify success: take post-snapshot, `mark_executed`
6. On verify failure: take failure snapshot, `mark_failed`, return diff between pre and post

**Inner CLI planner** — new `backend/orchestration/cli_configure_planner.py`:
- Same pattern as `configure_planner.py` (which is WebUI-side)
- Uses Haiku 4.5 (production rule)
- System prompt tells Haiku to output IOS XE commands only, no prose, end each line with newline-friendly syntax

**Tests**:
- Mock send_config_set + show_running_config
- Unit: propose stores config_commands; execute retrieves + sends + verifies
- Real-router: OSPF process 100 area 0 on Vlan1 → verify via `show ip ospf` → confirm via diff of running-config

**Why CLI is simpler than WebUI Phase 5**:
- No browser, no Playwright, no Chromium sessions
- No DOM, no spatial labels, no role+name matching
- One inner-LLM call per propose, one Netmiko call per execute
- Verification is regex against `show` output — deterministic
- Rollback via pre-snapshot is trivial (`copy startup-config running-config` or apply inverse commands)

## Third chunk — real-router demo prep (~1 hr)

After multi-propose chain + CLI configure both land, do an end-to-end demo:
1. **CLI**: "Configure OSPF process 100 area 0 on Vlan1" via `propose_cli_configure` → APPROVE → executes via Netmiko → verify via `show ip ospf`
2. **WebUI**: Same intent via `propose_webui_configure` → multi-propose chain → APPROVE → executes via Playwright → verify via `show ip ospf` + screenshot bundle

Compare evidence trails: both pre+post snapshots, CLI session log, screenshot bundle, structured execution report. Update the technical_report.md draft with both paths.

## Operating notes

- Worktree path: `C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5`
- Worktree venv: `.venv/Scripts/python.exe` (Windows PowerShell)
- Backend: `uvicorn backend.main:app --reload --port 8000`
- Frontend: `npm run dev` from `frontend/` (port 3000)
- Approval flow: INLINE in chat (no /preview round-trip)
- Conventional Commits + `ruff check && mypy && pytest -q` before every commit
- Pre-commit hook auto-formats — first commit may bounce; re-stage and re-commit (DO NOT `--amend`)
- Tags hands-off (Filip authorises milestones; daily backups autonomous)
- **Production LLM = Haiku 4.5 only** (memory rule, locked 2026-05-15)
- **Model role split for dev**: Opus plans, Sonnet implements step-by-step with tests interleaved, Haiku audits lightweight (memory rule)

=== END ===

## Today's open testable surface (what works as of 2026-05-15 evening)

Filip can test these end-to-end RIGHT NOW (proven by today's work):

**Fast-path CLI** (single command via chat):
- `zmeň hostname na FOO` → propose_set_hostname → APPROVE → set_hostname → verify
- `nastav IP 192.168.20.1/24 na Gi0/0/1` → propose_set_interface_ip → ...
- `pridaj VLAN 30 s názvom OFFICE` → propose_set_access_vlan → ...

**Fast-path WebUI** (single command, demo path):
- `zmeň hostname na BAR cez WebUI` → propose_webui_set_hostname → APPROVE → webui_set_hostname (proven during today's runaway debug — LAB-R4 → LAB landed in 23 sec)
- `pridaj VLAN 30 cez WebUI` → propose_webui_add_access_vlan → ...

**Read-only**:
- Any `show *` request — routed through CLI read tools
- Any "ako sa konfiguruje X" question — search_docs grounded answer with Sources section

**Phase 5 generic configure** (PARTIAL — single-step only):
- Works: anything that fits in ONE click on the landing page (e.g. "open the VLAN page" → navigate, but doesn't really configure)
- Doesn't work yet: anything requiring page transition (static route Add+fill, OSPF Add+configure, etc.)

**Recorder**:
- `.venv\Scripts\python.exe scripts\record_webui_catalog.py` — opens Chromium, logs in, auto-captures every page Filip visits (no more zombie-window bugs from the morning)

## Out of scope until Phase 6+

- Vision-on-demand (`webui_visual_check`)
- Two-phase approval (intent then plan separately)
- Selector allowlist enforcement
- Multi-device targeting
- Mid-flow CANCELLING state

These are tracked in [docs/plan-ai-first-webui.md](docs/plan-ai-first-webui.md) Phase 5.1+ deferred list.
