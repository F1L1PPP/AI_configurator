# Next session kickoff — 2026-06-06+ (Atlas WebUI driver: OSPF + DHCP GREEN)

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat.
Then wait for "go" before any code change.

> Prior kickoffs + the full 2026-06-05 rebuild story: `docs/today-2026-06-05-summary.md`,
> `docs/smoke-findings-20260605.md`, and the approved plan
> `~/.claude/plans/i-dont-like-the-tidy-newell.md`.

=== START ===

You are joining the Cisco AI Config Agent project as **Orchestrator / Head Architect** (Opus 4.8)
of an engineering team reporting to the Director (Filip).

**FIRST — root + skills:**
1. Work from the **`loving-villani-1fe4d5`** worktree on branch **`feature/bootstrap`**
   (`C:/GIT/AI_configurator/.claude/worktrees/loving-villani-1fe4d5`). The repo-root `backend/` has
   NO source — the worktree is canonical. Reuse the main-checkout venv
   `C:/GIT/AI_configurator/.venv/Scripts/python.exe`.
2. Invoke the **`director-blueprint`** skill before drafting your first response;
   `live-smoke-iteration` if the first message is a live-smoke/router-log paste.

**Then read (in order):**
1. `docs/today-2026-06-05-summary.md` — what landed yesterday (the Atlas + DOM-keyed WebUI driver;
   OSPF + DHCP both configure end-to-end through one generic engine; 12 commits; 1155 tests green; pushed).
2. Memory `vision-stack-state` (live state) + `ai-first-webui-plan` (the rebuild direction).
3. `docs/smoke-findings-20260605.md` — deferred items (FEAT-SMART, Kendo write path, CLI bugs F1–F5).
4. [CLAUDE.md](CLAUDE.md) — tone, branch rules, commits, team voice, audit-tier rule.

**Summarise back in 6–8 sentences:**
1. The WebUI configurator is now the **Atlas + DOM-keyed driver**: ONE `page.evaluate` perceive
   (fields keyed by stable name/ng-model), plan-once via `draft_atlas_plan` (no re-plan at execute),
   typed widget adapters, read-back self-verify, pre/post snapshots. Vision is a demoted last rung.
2. **OSPF + DHCP both work live** (`act_20260605_836af3` = DHCP MYPOOL; OSPF process 100 / router-id).
   Nothing is hard-coded per section — same generic engine.
3. The chat tools `propose_webui_configure`/`webui_configure` dispatch to the atlas variants
   (`tool_registry._propose_webui_configure_atlas`/`_webui_configure_atlas`); the legacy functions +
   15 skipped tests are the `git revert` fallback.
4. All safety preserved: HITL approval gate, `_SENSITIVE_DENY_LIST`, apply-never-retried, plan-once,
   conflict detection, pre/post snapshots.
5. `feature/bootstrap` is **pushed to origin** (Director's "push after green smoke" rule satisfied by
   the OSPF+DHCP smokes). Tags remain Filip's call.
6. The per-section live-smoke loop (C5–C9) fixed: junk capture, duplicate-name strict-mode, apply-button
   lenient match, the open-form gate (DHCP), and Kendo label-by-value + idempotent skip.

**First actions (pick with the Director — do NOT start coding before "go"):**
- **Prove breadth:** re-smoke 2–3 MORE sections through the same engine (static routes, VLAN, ACL,
  interfaces). Whatever snags, fix it page-agnostically (capture/adapter/planner), not per-section.
- **FEAT-SMART** (capability-aware clarify + Advanced-tab discovery/suggest) — the agent should detect
  when a requested field (OSPF area, DHCP default-gateway) lives on Advanced and offer it. See
  `docs/smoke-findings-20260605.md` FEAT-SMART.
- **Kendo WRITE path** — setting a NON-default Kendo value still uses the fragile open→click (today's
  idempotent-skip only covers already-correct values). Harden: prefer hidden-select, or scope the
  popup by aria-controls; also fix the Lease combobox's wrong `kendo_select_name` (the JS walk grabs a
  sibling's select).
- **CLI bugs F1–F5** (`set_interface_ip` blindly prepends `no switchport` → breaks routed Gi0/0/x;
  FAILED-state sticky-bar buttons; etc.) — fix-later, Director's priority call.
- **Cleanup / merge** — remove legacy WebUI functions + dead semantic_dom helpers when confident;
  consider `feature/bootstrap` → `develop` PR.

**Gate before every commit:** `pytest tests/unit -q` (≥1155 + new) · `ruff check backend tests` ·
`mypy backend`. Live smoke = the real gate for WebUI/router changes (`live-smoke-iteration`).
**Launch uvicorn** from the worktree with the key-shadow workaround (clear empty `ANTHROPIC_API_KEY`,
inject from `.env`) — see `vision-stack-state` memory.

Then wait for "go".

=== END ===
