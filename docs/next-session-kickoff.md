# Next session kickoff — 2026-05-31+ (vision: functional + quick)

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

> Full pre-2026-05-30 daily recaps + the complete 2026-05-23 vision-stack saga live in git
> history: `git show HEAD~1:docs/next-session-kickoff.md` (and the `docs/today-*.md` summaries).
> This doc carries forward the current handoff, the distilled lessons, and the live backlog.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream as the **Orchestrator / Head Architect** of an Engineering & Networking Team reporting to the Director (Filip).

**FIRST — root + skills:**
1. Work from the **`loving-villani-1fe4d5`** worktree on branch **`feature/bootstrap`** — the only place with the full vision-hybrid + the 5 unpushed commits. If your session didn't start there, `EnterWorktree` it (path `C:/GIT/AI_configurator/.claude/worktrees/loving-villani-1fe4d5`).
2. Check your skill list for the three project skills and invoke **`director-blueprint`** via the Skill tool BEFORE drafting your response. Invoke `live-smoke-iteration` if the first message is a live-smoke result / router-log paste; `external-review-triage` if a review summary table appears.

**Then read (in order):**
1. Memory **`ai-first-webui-plan`** — what we're building: the original AI-first WebUI plan (`docs/plan-ai-first-webui.md`) evolved into the vision-hybrid driver; goal = ONE Claude-Vision path that's **functional** (rescues what semantic-DOM misses, e.g. the DHCP "Subnet Mask" Kendo dropdown) **and quick** (no 30s stalls, no 6-min smokes).
2. **`docs/plan-vision-functional-quick.md`** — THE execution plan (code-level, files/functions named). Your work list.
3. Memory **`vision-stack-state`** — live state + machine quirks (key-shadow launch workaround, venv path; decays fast).
4. [CLAUDE.md](CLAUDE.md) — tone, branch rules, commits, Communication style (team voice, tradeoffs first, no fluff).
5. The "Carried-over vision lessons" section in THIS doc — load-bearing, especially *vision-from-screenshot can't see HTML attributes* and *hybrid eid-first → vision-fallback is the right shape*.

**Summarise back in 8-10 sentences:**
1. `feature/bootstrap` @ `9e55fdd`, **5 commits ahead of origin (UNPUSHED** — push only after a GREEN smoke, Filip's rule). 764 unit tests green, ruff clean (2026-05-30).
2. Goal: ONE vision path, **functional AND quick** — two qualities of the same path, not two tools.
3. Vision is ALREADY DOM-first → cached-vision-fallback, and the 30s→5s/4s timeout split is ALREADY done. The work is NARROW (fix + tune), not a rebuild.
4. **Functional blocker:** DHCP smoke fails on the "Subnet Mask" Kendo dropdown — `semantic_dom._resolve_name` names it by its value "255.255.255.0" not "Subnet Mask"; `_playwright_subprocess._kendo_select` throws `unknown_error`.
5. **Quick problem:** ~6-min smokes from over-eager proactive plan-vision + a poisoned `artifacts/selector_cache.json` (`vision_526b1241`) causing 30s stalls.
6. Director Blueprint — team voice, tradeoff tables before decisions; Audit tier rule (Haiku light / Opus deep; when in doubt deep; **NEVER skip deep audit on a smoke-touching chunk**).
7. The `nice-wing-7bd008` throwaway worktree was discarded 2026-05-30 (branch deleted; stray edits saved to `backups/nice-wing-stray-edits-20260530.patch`).
8. First actions below.

**First actions (in order):**
1. Confirm state: `git log --oneline origin/feature/bootstrap..feature/bootstrap` (expect 5) + `git status` (clean).
2. **Delete `artifacts/selector_cache.json`** (poisoned `vision_526b1241` → 30s stalls) — or land the Step 3.2 validate-on-load fix that makes this automatic.
3. **Chunk 1 — Kendo (functional)**: `semantic_dom.py:describe_page` label fix (name = "Subnet Mask", not "255.255.255.0", combobox-scoped) + `_playwright_subprocess.py:_kendo_select` 3-strategy rewrite that **lets Playwright timeouts bubble** (→ `element_intercepted`, kills `unknown_error`) + unit tests. **Deep audit (Opus)** — error-path-touching.
4. **Chunk 2 — Quick**: `plan_vision_check._plan_validation_signal` (`>=1 → Tier 0`) + cache validate-on-load + pre-trust probe + `_hash_page_url` fragment fix + smaller/faster vision calls (`viewport_only`, `timeout=20`, `max_retries=2`) + tests.
5. **Smoke** the DHCP intent (`Configure DHCP pool MYPOOL with network 20.20.20.0/24, default gateway 20.20.20.1`). Green = Subnet Mask named correctly, `_kendo_select` succeeds (no `unknown_error`), no 30s stalls, total << 6 min. Launch uvicorn with the key-shadow workaround (see `vision-stack-state`).
6. After green: push `feature/bootstrap` (Director-gated) → propose tag `v0.5.9-vision-hybrid` (Filip's call).

Then wait for "go". **Do not propose re-planning chunk order** — locked unless Filip asks.

=== END ===

---

## What landed 2026-05-30 (this session — memory + plan, NO code)

Orientation + planning. No code changed; nothing pushed.

- **Re-anchored "what we're doing":** the original AI plan (`docs/plan-ai-first-webui.md`) is superseded by the vision-hybrid driver. Captured as NEW memory **`ai-first-webui-plan`** (+ MEMORY.md index). Goal locked with Filip: ONE vision path, **functional AND quick**.
- **Wrote the execution plan** **`docs/plan-vision-functional-quick.md`** — code-level, grounded in the real `feature/bootstrap` code. Reframe: vision is already DOM-first → cached-fallback and the timeout split is already done, so the work is narrow (Kendo fix + cache hygiene + proactive-vision trim), NOT a rebuild.
- **Discarded the `nice-wing-7bd008` throwaway worktree** (auto-generated off `origin/main`; never had the vision code). Branch deleted + deregistered; stray "Agent-1" edits (a superseded 364-line vision-eid-eviction + timeout re-derivation) saved to `backups/nice-wing-stray-edits-20260530.patch`. Folder auto-removes when that session closes. The dangling `claude/trusting-colden-65e1ba` branch can also be `-D`'d.
- **Verified state:** `feature/bootstrap` @ `9e55fdd`, clean, 5 unpushed, 764 tests green.

### Bridge — what landed 2026-05-24 → 2026-05-29 (Phase A/B convergent driver)
Commits `d991bb3` (Phase A) + `0ffad84` (Phase B), the two newest unpushed:
- **Phase A — "faster, convergent configure driver":** forced-JSON inner planner, fail-fast timeouts + vision eviction, `no_progress` convergence guard, vision gating, empty-step reject, proposal rendering.
- **Phase B — "open the form before planning":** open the Add form at propose-time so the planner plans against the REAL fields (no more hallucinated field names); initial Kendo dropdown support; LLM-free open-form heuristic.
- Last smoke (`act_20260529_a88c57`): open-then-plan WORKS (real fields), `no_progress` works — but FAILS on the Subnet Mask Kendo dropdown and was slow (poisoned cache). Exactly what `docs/plan-vision-functional-quick.md` targets.

## Carried-over vision lessons (load-bearing — from the 2026-05-23 saga)

- **Vision-from-screenshot fundamentally can't see HTML attributes.** Asked for `input[name='X']`, Haiku falls back to `button:has-text(...)`, which misses Cisco's icon-only / nested-span buttons. Vision needs DOM context to produce attribute selectors.
- **Hybrid > pure-vision-first.** Order is eid forward-lookup FIRST (the describe view HAS attribute knowledge) → vision fallback (for elements describe drops) → `first_match` heuristics last. The 14g "vision-first inversion" was wrong and is obsolete.
- **Visibility is foundational.** Subprocess `vision_fallback_*` / `selector_cache_evicted` / `plan_vision_check_*` events are forwarded to the parent uvicorn log (14h-C). USE them. Rule: if two consecutive smokes fail with the same generic symptom, ship the visibility fix before any more architecture changes.
- **Cache hygiene = over-evict, don't under-evict.** `unknown_error` is in the eviction STALENESS set so a bad cached selector self-heals. The plan adds validate-on-load + a pre-trust probe so poison can't cause a 30s stall in the first place.
- **Default-PROCEED on vision failure paths** (API error / 529 / JSON parse). The approval gate + action store are the safety net, not the pre-check.
- **The wiring trap.** For every new function/contract, GREP for runtime callers before commit (14k shipped dead code because the audit was skipped on "small surface"). NEVER skip deep audit on a smoke-touching chunk.

## Remaining chunks (one-line each)

| # | Chunk | Phase | Est | Pri |
|---|---|---|---|---|
| **Vision f+q** | Kendo fix + cache/latency per `docs/plan-vision-functional-quick.md` → green DHCP smoke | G | ~1 day | **HIGH (first action)** |
| 14h-A (deferred) | Vision-ground `configure_planner` (screenshot + element list + RAG + DOM → stop broken-plan emission at the source) | G | ~4-6 h | MED (upstream root-cause) |
| 14h-G / 14g | LOW audit follow-ups: vision-path deny-list fail-open + `_eid_for_intent` tie-break filter; pre-check polish (counter leak, proposal-cap, shared json-extract, atomic cache) | G | ~2 h | LOW/MED |
| 14c | Vision-fallback polish (secret-page deny-list, offline corpus bootstrap) | G | ~2-3 h | MED |
| 15 | Hardware retests — OSPF + ISIS WebUI on live router (unblocked once DHCP green) | F | ~30 min | MED |
| 17 | Cosmetic prototype-label sweep | F | ~10 min | LOW |
| 18 | Cut clean `v0.4.0-alpha.1` consolidation tag | F | ~15 min | — |
| — | #8 SecretStr migration (deferred from 2026-05-21 review) | — | ~1 h | MED |
| — | Pre-demo hardening MED + LOW batches (below) | mixed | ~2 h | mixed |
| — | Dead-code sweep — `docs/dead-code-findings.md` (run after the driver is green) | — | ~2 h | LOW |

## Notes / housekeeping

- **`nice-wing-7bd008`**: discarded 2026-05-30 (branch gone, deregistered). The empty folder auto-removes when the session that holds it closes; delete manually if it lingers. Optional: `git branch -D claude/trusting-colden-65e1ba`.
- **Env quirks** (from `vision-stack-state`): clear the empty `ANTHROPIC_API_KEY` shell-shadow then inject from `.env` before launching uvicorn; venv = main-checkout `.venv` (Python **3.13** vs CLAUDE.md-locked **3.12** — reconcile before any tag). Find + fix the `Set-Location`/env hook that injects the empty key.
- `backups/nice-wing-stray-edits-20260530.patch` is the discarded nice-wing diff — gitignored, pure safety net, delete whenever.
- `tools/check_vectorstore.py` / `tools/query_rag.py` flagged in the 2026-05-19 dead-code audit — non-blocking follow-up.
- Director Blueprint applies from message #1. Prefer invoking the `director-blueprint` skill over re-reading the memory file.

## Pre-demo hardening (verified at v0.5.5 — pick up before cutting `v0.4.0-alpha.1` or any external sharing)

Both `/review` + `/security-review` concluded **ship as-is for the alpha-1 demo — nothing attacker-reachable on single-operator localhost** — but flagged ~8 items (~2 h).

### MEDIUM
- **[SEC-A]** `routes_snapshots.py:~47` — add `.resolve().is_relative_to(settings.artifacts_dir)` after path construction (regex + phase check are tight but add defence-in-depth). ~10 min + 1 test.
- **[SEC-B]** `routes_suggestions.py:~69` — add `# SECURITY: POSITIVE ALLOWLIST` comment to `_build_digest` (only hostname / vlan / interface-ip are extracted; never secrets). ~5 min.
- **[QUAL-2]** `write_tools.py:~344` — `_verify_running_config` does an unconditional SSH round-trip per write; skip when the netmiko-output check was clean, or shorten the success-path read timeout 60s→5s. ~20 min.

### LOW (batch)
- **[QUAL-3]** `frontend/screen-preview.jsx:123-125` — three placeholder Back/Approve/Apply buttons that only `console.warn`; wire or delete.
- **[QUAL/SEC-4]** `routes_devices.py:_enrich_with_show_version` — SSH `show version` per request; add a 30s cache (mirror `routes_suggestions._cache`).
- **[QUAL-5]** `tests/unit/test_conflict_detector.py` — add a nested `address-family ipv4 vrf <name>` BGP test.
- **[SEC-C]** `write_tools.py` verify_failed — redact raw IOS `%` error lines from chat events (leak hostnames/VRF); log full text, surface category only.
- **[SEC-E]** `routes_snapshots.py:~59` — cap snapshot response (`MAX_SNAPSHOT_BYTES` ~64 KB) + `{truncated: true}`.
- **[SEC-G]** `routes_chat.py:~111` — move Anthropic `request_id` from the 503 body to `log.warning`; surface `req_***`.
- **[QUAL-1/SEC-F]** `_emit_cli_commands` fires BEFORE the SSH send in all 4 write tools — document that the live stream is INTENT, not wire-confirmation (`docs/how-it-works.md`).
- **[SEC-H]** (separate WebUI hardening pass, pair with the vision work since both touch `backend/webui_agent/`): origin allowlist, sensitive-element deny-list, secret redaction, `propose_webui_configure` scope.

## Post-roadmap polish (after all phases land — none blocking)

- **Wire `device.id` into `fetchSuggestions`** — `frontend/screen-ai.jsx:232-244` calls `fetchSuggestions()` with no arg (defaults to `router-01`). Correct today (single-device lab); when multi-device discovery lands, thread the selected `device.id` + add it to the `useEffect` deps. Backend cache is already keyed by `device_id`.
- **Tighten Haiku suggestion grounding** — `routes_suggestions._build_digest` includes `router ospf 1` but Haiku still suggests "Enable OSPF" though it's active. Add an explicit `OSPF: process N active` digest line. ~15 min.
