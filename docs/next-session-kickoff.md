# Next session kickoff — 2026-05-19+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. Read these five docs first, then summarise back in 6-8 sentences:

1. [docs/today-2026-05-15-summary.md](docs/today-2026-05-15-summary.md) — 2026-05-15 morning wrap (Phase 3.3+3.4+5 + Haiku-only swap)
2. [docs/today-2026-05-15-evening-summary.md](docs/today-2026-05-15-evening-summary.md) — 2026-05-15 afternoon wrap (multi-propose chain + CLI configure + 4 real-router fixes, alpha.1/.2/.3 tags)
3. [docs/today-2026-05-18-summary.md](docs/today-2026-05-18-summary.md) — 2026-05-18 wrap (settle-wait fix + alpha.4 tag + design handoff doc + ISIS retest blocked by Anthropic 529s)
4. [docs/plan-ai-first-webui.md](docs/plan-ai-first-webui.md) — the v0.4.0 phase plan
5. [docs/design-handoff.md](docs/design-handoff.md) — design redesign brief (parallel-track; designer working on mockups)

After reading, summarise:
1. The state of `feature/bootstrap` at HEAD (`115fc2d`) — 500 tests passing, 5 v0.4.0-alpha.* tags on origin, ISIS retest pending due to Anthropic 529s
2. What `_settle_page` does and which class of bug it solves (modal disappearance race between action and re-describe)
3. The Anthropic 529 overload events on 2026-05-18 — why they blocked verification, why retry hardening is the FIRST chunk of this session
4. The unverified-but-deployed fixes that need hardware retest: ISIS WebUI (alpha.4), OSPF WebUI (alpha.3)
5. The design redesign is a parallel track — designer has [docs/design-handoff.md](design-handoff.md), no mockups yet, frontend stable

Then wait for "go" before making any change. Don't propose re-planning — the plan is locked unless Filip asks.

## Today's first chunk — Anthropic 529 retry hardening (~20 min)

**Why FIRST**: yesterday's ISIS retest failed because Anthropic's API returned `OverloadedError` (HTTP 529) on the outer and inner planner LLM calls. The SDK's default `max_retries=2` ran out in ~2-3s, then the whole flow bombed out as `tool_failed`. Until this is hardened, we can't reliably retest ANY hardware flow during overload windows.

**Three changes**:

1. **Bump `max_retries=5`** at every `Anthropic()` client construction site:
   - `backend/orchestration/planner.py` (the outer planner client)
   - `backend/orchestration/configure_planner.py:draft_plan` (inner WebUI planner — currently constructs client lazily inside the function)
   - `backend/orchestration/cli_configure_planner.py:draft_cli_plan` (inner CLI planner — same lazy construction)

   The Anthropic SDK's exponential backoff with max_retries=5 gives roughly 0.5s + 1s + 2s + 4s + 8s = ~15s of retrying instead of ~2-3s. Most transient 529s clear within that window.

2. **Friendly error wrapping** in `_webui_configure` and `_cli_configure` for `OverloadedError`:
   ```python
   from anthropic import OverloadedError
   ...
   except OverloadedError as exc:
       return {
           "error": "llm_overloaded",
           "message": (
               "Anthropic API temporarily overloaded — retry in 1-2 minutes. "
               "Your action_id is preserved; clicking EXECUTE again will start fresh."
           ),
           "request_id": getattr(exc, "request_id", None),
       }
   ```
   The chat UI's existing "✗ SERVER ERROR" panel will display the friendly message instead of the raw JSON traceback.

3. **Same wrapping in `propose_webui_configure` and `propose_cli_configure`** — covers the case where the outer planner survives but the inner planner 529s during propose.

**Tests**: mock `Anthropic.messages.create` to raise `OverloadedError`, assert each propose/configure tool returns `{"error": "llm_overloaded", ...}` instead of letting the exception propagate as `tool_failed`. ~3-5 regression tests across `test_configure_planner.py`, `test_cli_configure_planner.py`, `test_tool_registry_phase5.py`.

**Tag after landing**: `v0.4.0-alpha.5-overload-retry`.

## Second chunk — ISIS WebUI hardware retest (~15 min)

**Why**: alpha.4's `_settle_page` is deployed but unverified end-to-end. The 2026-05-18 retest got blocked at the LLM layer (529s), not at the Playwright/modal layer. Need to confirm the settle wait actually keeps the ISIS Add modal open long enough for `describe_page` to capture the form.

**Steps**:
1. Confirm Anthropic API is healthy (https://status.anthropic.com).
2. Restart uvicorn (alpha.4 is in HEAD; auto-reload should have picked it up but a clean restart removes any doubt).
3. From chat: `Nakonfiguruj ISIS proces s názvom A cez WebUI`.
4. Expected flow:
   - propose_webui_configure → inner Haiku drafts `[click Add]`
   - APPROVE → EXECUTE
   - Iter 1: click Add → `_settle_page` waits → modal stable → re-describe captures form fields (Router ISIS textbox, Level dropdown, Net Area, Net IP Address, Apply to Device button)
   - Iter 2: inner Haiku drafts fill plan: `Router ISIS=A`, `Net Area=49.0001`, `Net IP Address=0000.0000.0001.00`, click `Apply to Device`
   - Verify "A" present in the ISIS table → `mark_executed`
5. Verify on device: `show isis protocol | include System Id`. Should print `System Id: 0000.0000.0001`.

**If iter 2 still gets `inner_plan_empty`**: the modal closes faster than the 500ms fallback handles. Bump `_SETTLE_FALLBACK_MS` to 1000ms in `_playwright_subprocess.py` and retest. Don't go above 2000ms without revisiting the per-step cost budget.

**If iter 2 fills fields but the labels are weird** (e.g., "Net" is split across `Area` + `IP Address` columns and the spatial-label fix doesn't catch them as separate labelled fields): the issue is form-specific. Add an ISIS example to the inner WebUI prompt's field-mapping section showing how to split a NSAP address `49.0001.0000.0000.0001.00` into `Area=49.0001` + `IP Address=0000.0000.0001.00`.

## Third chunk — OSPF WebUI hardware retest (~15 min)

**Why**: alpha.3's click-Add rule was tested in unit tests but not on hardware for OSPF specifically. Yesterday's session moved straight to ISIS without revisiting OSPF.

**Steps**:
1. Check existing OSPF processes: `show ip ospf | include Routing Process`.
2. Pick a process ID that doesn't clash (e.g., 8 or higher if 2/3/4/5/100 are in use). Pick a router-id that's NOT 10.0.0.1 (already in use by ospf 2).
3. From chat: `Nakonfiguruj OSPF process 8 s router ID 10.0.0.8 cez WebUI`.
4. Expected flow: same shape as ISIS — click Add → form opens → fill Process ID + router-id → Apply.
5. Verify: `show ip ospf | include Routing Process` should now include `Routing Process "ospf 8"`.

## Fourth chunk — router-id conflict pre-check (~45 min)

**Why**: alpha.3's `device_errors` field surfaces Cisco rejections AFTER execute. Better UX: refuse at propose time when the operator picks a router-id already in use.

**Fix path** (`backend/orchestration/cli_configure_planner.py:_INNER_SYSTEM_PROMPT`):

Add a "Pre-flight conflict check" rule:
> Before drafting `config_commands`, scan the provided running-config for conflicts with the intent's unique identifiers:
> - **router-id**: if the intent specifies `router-id X.X.X.X`, grep running-config for existing `router-id X.X.X.X` lines. If found, return `config_commands: []` with `risk: "Conflict — router-id X.X.X.X already in use by <process>. Pick a different router-id or remove the existing process first."`.
> - **interface IP**: similar grep for `ip address X.X.X.X` collisions.
> - **VLAN ID**: similar grep for `vlan X` collisions.
> - **OSPF/EIGRP/RIP process IDs**: similar grep.

The outer Haiku Rule 8 already handles the FINAL signal — when propose_cli_configure returns `intent_not_mappable` (or a similar error), the chat surfaces the conflict message without retrying.

**Tests**: mock `show_running_config` to return a config with `router ospf 2 / router-id 10.0.0.1`. Inner LLM (mocked) should refuse with empty config_commands + a conflict-flavored risk note. Add regression to `test_cli_configure_planner.py`.

**Real-router validation**: ask `Configure OSPF process 9 with router-id 10.0.0.1` (intentional collision). Should refuse at propose time, NO action_id created, user sees the conflict message in chat.

## Fifth chunk — consolidate to v0.4.0-alpha.1 milestone tag (~15 min)

**Why**: CLAUDE.md §72 references the un-suffixed name `v0.4.0-alpha.1` for the scope-lock release. Five iterative alpha.* tags with suffixes exist; the formal milestone tag does not yet.

**Steps**:
1. Confirm chunks 1-4 above all landed and validated on hardware.
2. Filip authorises milestone tag (CLAUDE.md tag rule — he creates them or explicitly authorises Claude to create).
3. Tag the consolidated milestone:
   ```
   git tag -a v0.4.0-alpha.1 <HEAD> -m "v0.4.0-alpha.1: AI-first Cisco configure agent validated on hardware"
   git push origin --tags
   ```
4. Update CLAUDE.md §72 — remove or move the scope-lock note now that the tag exists.

## Open backlog (pick one if time permits)

- **Resume from mid-flow on retry**: if iter 1 succeeded but iter 2's LLM call 529s, the next EXECUTE click could pick up from iter 2 instead of starting over. Requires persisting `executed_steps` to the action store. Bigger change — only do if 529s become a daily problem.
- **Iteration cap evaluation**: cap=4 may be tight for complex flows (OSPF with multiple network statements + interface assignments). Bump to 6 if any flow hits the cap.
- **`| section` → `| include` for BGP/route-map**: alpha.2 fixed OSPF; other features may have the same gotcha.
- **Recorder catalog refresh**: re-run `scripts/record_webui_catalog.py` to capture the ISIS page elements properly. The outer nav map currently points at `/webui/#/isis` correctly but doesn't know about the per-protocol Add modals.
- **Phase 6 — Vision on-demand** (canonical next phase after v0.4.0-alpha.1): `webui_visual_check(question)` tool with screenshot + bbox overlay + image content block. Would catch the shift-by-one-row class of bug AND verify modal-state checks. ~0.5 day. See [plan-ai-first-webui.md §201](plan-ai-first-webui.md).

## Design redesign (parallel track, informational)

Designer working on mockups. Has [docs/design-handoff.md](design-handoff.md) which covers the full UI map, component inventory, approve/execute flow, and design principles. Five open questions to resolve with the designer before committing to a final design:

1. Live event stream placement (right panel current vs bottom drawer vs separate route).
2. Action ID prominence (chip / expanded / hover-only).
3. Chromium-during-execute as a separate OS window (current) or embedded.
4. Failed-action info hierarchy (error → device_errors → snapshots → full output).
5. Slovak/English UI strings.

When mockups arrive: that's a separate chunk, separately scoped. Until then, the frontend is stable; no code work needed for the designer.

## Operating notes

- Worktree path: `C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5`
- Worktree venv: `.venv\Scripts\python.exe` (Windows PowerShell)
- Backend: `.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000`
- Frontend: `npm run dev` from `frontend/` (port 3000)
- Approval flow: INLINE in chat (no /preview round-trip)
- Conventional Commits + `ruff check && mypy && pytest -q` before every commit
- Pre-commit hook auto-formats — first commit may bounce; re-stage and re-commit (DO NOT `--amend`)
- **Tags**: Filip authorises milestone tags. Daily `backup-YYYYMMDD-HHMM` tags are autonomous via `/checkpoint`. The harness classifier blocks unauthorised tag creation. Filip explicitly authorised the alpha.* tags created in this chain.
- **Production LLM = Haiku 4.5 only** (memory rule, locked 2026-05-15)
- **Per-turn propose quota** (locked 2026-05-15 evening): at most ONE call to each propose_* tool per turn. `verify_failed`/`empty_plan`/`unsafe_command` are FINAL.
- **Settle-wait is load-bearing** (added 2026-05-18): every successful WebUI action gets a settle pass before re-describe. Networkidle 1500ms + 500ms fallback. Bump constants if a future Cisco page needs more.

=== END ===

## Release tag chain on origin

| Tag | Commit | What it fixes |
|---|---|---|
| `v0.4.0-alpha.1-ai-configure` | `b5a88a4` | First hardware-validated cut: multi-propose chain + CLI configure + spatial-label fix + CIDR splitting + null-verify loop continuation |
| `v0.4.0-alpha.2-retry-guard` | `50e09c3` | Per-turn propose quota + OSPF `\| section` → `\| include` |
| `v0.4.0-alpha.3-add-button` | `be4e7fd` | Inner planner clicks Add when intent says add + `device_errors` surface % lines |
| `v0.4.0-alpha.4-settle-wait` | `c96b653` | `_settle_page` between action and re-describe — survives Cisco's auto-dismiss modal race (ISIS) |

Next planned: `v0.4.0-alpha.5-overload-retry` after chunk 1 lands. Then consolidated `v0.4.0-alpha.1` (no suffix) once chunks 2-4 are hardware-validated.

## Today's open testable surface

Filip can test these end-to-end RIGHT NOW (proven on the C1111 lab router this session series):

**Fast-path CLI** (single command via chat):
- `zmeň hostname na FOO` → propose_set_hostname → APPROVE → set_hostname → verify
- `nastav IP 192.168.20.1/24 na Gi0/0/1` → propose_set_interface_ip → ...
- `pridaj VLAN 30 s názvom OFFICE` → propose_set_access_vlan → ...

**Fast-path WebUI**:
- `zmeň hostname na BAR cez WebUI`
- `pridaj VLAN 30 cez WebUI`

**Generic WebUI (multi-propose chain)** — VALIDATED:
- Static route: `pridaj statickú trasu 10.99.99.0/24 cez 192.168.10.254 cez WebUI` ✅

**Generic WebUI** — PENDING hardware retest (alpha.3 + alpha.4 deployed, not verified):
- ISIS: `Nakonfiguruj ISIS proces s názvom A cez WebUI`
- OSPF: `Nakonfiguruj OSPF process N s router ID 10.0.0.N cez WebUI`

**Generic CLI (AI configure)** — VALIDATED:
- Trunk port, OSPF process 100, hostname rename via generic path

**Read-only**:
- Any `show *` request, any `ako sa konfiguruje X` question

**Recorder**:
- `.venv\Scripts\python.exe scripts\record_webui_catalog.py`

## Out of scope until Phase 6+

- Vision-on-demand (`webui_visual_check`)
- Two-phase approval (intent then plan separately)
- Selector allowlist enforcement
- Multi-device targeting
- Mid-flow CANCELLING state
- Auto-rollback
- Mid-flow LLM-failure resumption

These are tracked in [plan-ai-first-webui.md](plan-ai-first-webui.md) Phase 5.1+ deferred list.
