# Next session kickoff — 2026-05-16+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. Read these four docs first, then summarise back in 5-7 sentences:

1. [docs/today-2026-05-15-summary.md](docs/today-2026-05-15-summary.md) — yesterday morning's wrap (12 commits, Phase 3.3+3.4+5+runaway-fixes-and-Haiku-swap)
2. [docs/today-2026-05-15-evening-summary.md](docs/today-2026-05-15-evening-summary.md) — yesterday evening's wrap (10 commits, multi-propose chain + CLI AI configure + 4 real-router-driven fixes + 3 release tags)
3. [docs/plan-ai-first-webui.md](docs/plan-ai-first-webui.md) — the v0.4.0 phase plan
4. [docs/security-review-2026-05-14.md](docs/security-review-2026-05-14.md) — the open hardening backlog

After reading, summarise:
1. The state of `feature/bootstrap` at HEAD (`be4e7fd`) — what works end-to-end on the C1111, what's known-broken
2. The three `v0.4.0-alpha.*` release tags on origin and what each one fixes
3. The OSPF flow gap (WebUI side needs hardware retest with alpha.3 fixes; CLI side has a router-id-reuse blind spot)
4. What's locked vs unlocked for scope (CLAUDE.md §72 referenced "six scenarios" until v0.4.0-alpha.1 tagged — three alpha.* tags now exist, so the scope-lock is effectively lifted)

Then wait for "go" before making any change. Don't propose re-planning — the plan is locked unless Filip asks.

## Today's first chunk — validate OSPF WebUI end-to-end (~30 min)

**Why**: alpha.3's rule-3 split for the inner WebUI prompt says "if intent contains add/create + Add button visible → draft `[click Add]` first iteration, fill form in iter 2". Yesterday's hardware test of OSPF via /webui/#/OSPF was BEFORE this fix landed and got an empty-plan response. The fix is committed and tagged but not yet hardware-validated for OSPF.

**Steps**:
1. Restart uvicorn (alpha.3 = `be4e7fd` is HEAD; auto-reload may have picked it up already).
2. From chat: `Nakonfiguruj OSPF process 6 cez WebUI` (router-id can be 10.0.0.6 to avoid clashing with the existing ospf 2 at 10.0.0.1 and any other process you've added during testing — check with `show ip ospf | include Routing Process` first).
3. Expected flow:
   - Outer Haiku → `propose_webui_configure(intent, webui_path=/webui/#/OSPF)`
   - Inner Haiku sees OSPF list page + Add button → drafts `[click Add]` with verify_text=null
   - APPROVE / EXECUTE → multi-propose loop runs
   - Iter 1: click Add → form appears
   - Iter 2: inner Haiku sees form fields → drafts fill plan with verify_text="ospf 6"
   - Verify reads `Routing Process "ospf 6"` from `show ip ospf | include Routing Process` (use this exact command — alpha.2 prompt fix). Or page text in WebUI.
   - `mark_executed`, Chromium closes
4. If iter 2 mis-maps field values (like static route's IP Type/Prefix collision did before the spatial-label fix), capture the executed plan from the structured log and we'll add another inner-prompt example.

**Test plan if it works**: confirm via `show ip ospf | include Routing Process` from chat. Should show ospf 6 alongside any other configured processes.

## Second chunk — close the OSPF router-id reuse blind spot (~45 min)

**Why**: yesterday's CLI OSPF process 5 attempt failed because router-id 10.0.0.1 was already in use by ospf 2. The `cli_configure` execution returned `verify_failed` (correct), and alpha.3 added the `device_errors` field to surface Cisco's `% Router-ID 10.0.0.1 in use` message — but the inner LLM at propose time should have caught this BEFORE the propose ever reached the human.

**Fix path**:
- The inner CLI planner already receives the live running-config in its prompt context. It just doesn't use it carefully.
- Inner prompt update (`backend/orchestration/cli_configure_planner.py:_INNER_SYSTEM_PROMPT`): add a "Pre-flight conflict check" rule:
  - When intent specifies a router-id, IP address, VLAN ID, or any other unique identifier, SCAN the running-config first.
  - If the identifier is already in use, return `config_commands: []` with risk = `"Conflict — router-id <X> already in use by ospf process <Y>. Pick a different router-id or remove the existing process first."`
- The outer Haiku will surface that refusal cleanly (Rule 8 handles the FINAL signal).

**Tests**:
- Mock `show_running_config` to return a config containing `router ospf 2 / router-id 10.0.0.1`. Inner LLM (mocked) should refuse with empty config_commands and a conflict message in risk.
- Add a regression to `test_cli_configure_planner.py`.
- Real-router: ask `Configure OSPF process 7 with router-id 10.0.0.1` (collision intentional). Should refuse at propose time, NO action_id created.

**Why not in alpha.3**: this needs inner-prompt iteration + real-router validation, and we already shipped alpha.3 with the device_errors safety net which handles the case after the fact. Pre-flight is better UX but not a security requirement.

## Third chunk — consolidate to v0.4.0-alpha.1 milestone tag (~15 min)

**Why**: CLAUDE.md §72 said "six scenarios in PROJECT_PLAN.md §2 only until v0.4.0-alpha.1 tagged". We have iterative alpha.1/.2/.3 with `-suffix` names but no consolidated `v0.4.0-alpha.1` milestone tag (the formal one referenced in CLAUDE.md). Once OSPF WebUI is hardware-validated (chunk 1) AND the router-id conflict check lands (chunk 2), the moment is right.

**Steps**:
1. Confirm OSPF WebUI works on hardware (chunk 1 outcome).
2. Confirm router-id conflict refusal works (chunk 2 outcome).
3. Filip authorises milestone tag (CLAUDE.md tag rule — he creates them).
4. Tag the consolidated milestone:
   ```
   git tag -a v0.4.0-alpha.1 <HEAD> -m "v0.4.0-alpha.1: AI-first Cisco configure agent validated"
   ```
5. Push.
6. Update CLAUDE.md §72 to remove the scope-lock now that the tag exists. Or move that line to a "completed milestones" section.

## Fourth chunk — open backlog (if time permits)

Pick one:
- **Iteration cap=4 evaluation**: OSPF with router-id + network statements + interface assignment may legitimately need 5-6 iterations. Bump to 6 if any real-world flow hits the cap.
- **Section vs include for BGP/route-map**: alpha.2 fixed `| section` → `| include` for OSPF. Other features may have the same gotcha. Check what BGP/route-map `show` commands look like and add to the gotchas list if needed.
- **Recorder catalog refresh**: re-run `scripts/record_webui_catalog.py` to capture the OSPF page elements properly (so the outer nav-map points at the right URL).
- **Snapshot diff view**: when `verify_failed` returns, the snapshots are saved but there's no UI to view the pre/post diff. A simple `GET /api/actions/{id}/diff` endpoint would help debugging.

## Operating notes

- Worktree path: `C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5`
- Worktree venv: `.venv\Scripts\python.exe` (Windows PowerShell)
- Backend: `.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000`
- Frontend: `npm run dev` from `frontend/` (port 3000)
- Approval flow: INLINE in chat (no /preview round-trip)
- Conventional Commits + `ruff check && mypy && pytest -q` before every commit
- Pre-commit hook auto-formats — first commit may bounce; re-stage and re-commit (DO NOT `--amend`)
- **Tags**: Filip authorises milestone tags. Daily `backup-YYYYMMDD-HHMM` tags are autonomous via `/checkpoint`. The harness classifier blocks LLM tag creation by default — Filip ran the three alpha.* tag commands manually yesterday after explicit authorisation.
- **Production LLM = Haiku 4.5 only** (memory rule, locked 2026-05-15)
- **Model role split for dev**: Opus plans, Sonnet implements step-by-step with tests interleaved, Haiku audits lightweight (memory rule)
- **Per-turn propose quota** (locked 2026-05-15 evening): at most ONE call to each propose_* tool per turn. `verify_failed`/`empty_plan`/`unsafe_command` are FINAL — don't retry.

=== END ===

## Today's open testable surface (what works as of 2026-05-15 evening)

Filip can test these end-to-end RIGHT NOW (proven on the C1111 lab router this session):

**Fast-path CLI** (single command via chat):
- `zmeň hostname na FOO` → propose_set_hostname → APPROVE → set_hostname → verify
- `nastav IP 192.168.20.1/24 na Gi0/0/1` → propose_set_interface_ip → ...
- `pridaj VLAN 30 s názvom OFFICE` → propose_set_access_vlan → ...

**Fast-path WebUI** (single command, demo path):
- `zmeň hostname na BAR cez WebUI` → propose_webui_set_hostname → ...
- `pridaj VLAN 30 cez WebUI` → propose_webui_add_access_vlan → ...

**Read-only**:
- Any `show *` request — routed through CLI read tools
- Any "ako sa konfiguruje X" question — search_docs grounded answer with Sources section

**Generic WebUI (multi-propose chain)** — VALIDATED today:
- `pridaj statickú trasu 10.99.99.0/24 cez 192.168.10.254 cez WebUI` — all 5 form fields filled correctly, verify passes. ~45s end-to-end.

**Generic CLI (AI configure)** — VALIDATED today:
- `nakonfiguruj trunk port s povolenými všetkými VLAN na Gi0/1/3` — applied on device. Verify passes after alpha.2 prompt fix.
- `nakonfiguruj OSPF process 100 area 0 na Vlan1 cez CLI` — applied on device. Verify passes with `show ip ospf | include Routing Process`.

**Generic WebUI for OSPF** — needs hardware retest tomorrow:
- alpha.3's click-Add rule should resolve. Use a router-id that's NOT already in use (check via `show ip ospf | include Routing Process` first).

**Recorder**:
- `.venv\Scripts\python.exe scripts\record_webui_catalog.py` — opens Chromium, logs in, auto-captures every page Filip visits

## Out of scope until Phase 6+

- Vision-on-demand (`webui_visual_check`)
- Two-phase approval (intent then plan separately)
- Selector allowlist enforcement
- Multi-device targeting
- Mid-flow CANCELLING state
- Auto-rollback (a separate `propose_cli_rollback(action_id)` could land later; not in v0.4.x)

These are tracked in [docs/plan-ai-first-webui.md](docs/plan-ai-first-webui.md) Phase 5.1+ deferred list.

## Release tag chain on origin

| Tag | Commit | What it fixes |
|---|---|---|
| `v0.4.0-alpha.1-ai-configure` | `b5a88a4` | First hardware-validated cut: multi-propose chain + CLI configure + spatial-label fix + CIDR splitting + null-verify loop continuation |
| `v0.4.0-alpha.2-retry-guard` | `50e09c3` | Per-turn propose quota + OSPF `| section` → `| include` |
| `v0.4.0-alpha.3-add-button` | `be4e7fd` | Inner planner clicks Add when intent says add + `device_errors` surface % lines |

Next planned: consolidated `v0.4.0-alpha.1` (no suffix) once OSPF WebUI is hardware-validated and the router-id conflict check lands.
