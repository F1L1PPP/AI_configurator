# Session summary — 2026-05-30

Worktree: `C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5` (branch `feature/bootstrap`). Companion: `project_vision_stack_state` memory (auto-loads the state + first actions).

## What was done

1. **Onboarded the fresh PC.** Reconstructed the 3 project skills (`director-blueprint`, `external-review-triage`, `live-smoke-iteration`) from docs, restored the Director-Blueprint memory files, set the git identity (`Filip <filipamatus44@gmail.com>`), and pushed the skills-restore commit. Skills now live in `.claude/skills/` so future clones keep them.

2. **Phase 0 env bring-up.** Reused the main-checkout venv, copied `.env` into the worktree, confirmed the router reachable (22/443). **Found a machine quirk:** an empty `ANTHROPIC_API_KEY` shell shadow (a `Set-Location`/env hook injects it) overrides the `.env` → boot fails the new fail-fast cred check. Workaround at launch: clear it + inject the real key from `.env`.

3. **Diagnosed the DHCP smoke.** eid-first resolution of the **Add** button already works (the 2026-05-23 vision fix is good). The real failure: the planner drafted against the DHCP **list** page and **hallucinated field names** → wrong fills → `iteration_cap_hit`, ~6 min.

4. **Phase A — fast + convergent driver** (commit `d991bb3`, 725 tests). 4 parallel Sonnet implementers → Opus 4.8 deep audit → remediation. Forced-JSON planner (no more prose-recovery), fail-fast act timeouts + vision-eid eviction, `no_progress` early-abort, per-iteration vision gated to ≤1, empty-step rejection, proposal-step rendering. (Audit caught a wrong-worktree drift + a cap-too-low + a vision-slot bug — all fixed.)

5. **Phase B — open-then-plan + Kendo** (commit `0ffad84`, 764 tests). At propose time, when the view is a list page (trigger button present, no submit button), open the Add form via a HITL-safe helper (read-only modal open), re-describe, and plan against the **real** fields. Kendo dropdown support in `describe_page` + the `select` action. Planner prompt: `select` for dropdowns, one-value-per-field, exclude→Starting/Ending-ip. LLM-free open-form heuristic (removed the extra per-propose Haiku call). Opus 4.8 deep audit: form-open helper security = PASS; heuristic hardened to a submit-button signal.

6. **Opus 4.7 → 4.8 relabel** across the 3 skills + `CLAUDE.md` + `CLAUDE_INSTRUCTIONS.md` (commit `438977c`).

7. **Final smoke `act_20260529_a88c57` — partial.** ✅ Open-then-plan WORKS (proposal drafted the real fields). ✅ `no_progress` + eviction work. ❌ Failed on **Subnet Mask (Kendo dropdown)**: `unknown_error`, and the field was named after its **value** ("255.255.255.0") not the label "Subnet Mask". 🐢 Slow: a stale poisoned `selector_cache.json` entry (`vision_526b1241` for "Network") caused a 30s click-timeout; Pool Name fill ~36s.

8. **Dead-code audit** (25 Opus 4.8 read-only agents): 60 confirmed-dead, 11 false-positives dropped → saved to `docs/dead-code-findings.md`, plus a reusable `dead-code-audit` skill (commit `e5bffe2`).

**Commits this session (LOCAL on `feature/bootstrap`, NOT pushed):** `46cdbc0` (skills) · `438977c` (4.8) · `d991bb3` (Phase A) · `0ffad84` (Phase B) · `e5bffe2` (dead-code skill+findings). 764 unit tests green, ruff clean. Push is **held** until a green smoke; tags hands-off (Filip's).

## What needs to be done next session (in order)

1. **Pre-smoke:** delete `artifacts/selector_cache.json` (poisoned `vision_526b1241` → 30s timeouts; it self-heals but burns time).
2. **Fix the Kendo Subnet Mask** (the blocker) — `semantic_dom.py` + `_playwright_subprocess.py`:
   - (a) **Label:** the dropdown is named after its current value ("255.255.255.0") instead of "Subnet Mask" — fix `_resolve_name` for the Kendo widget.
   - (b) **Select:** `_kendo_select` throws `unknown_error` — fix the actual set mechanism (needs the live widget DOM; likely click-the-widget-then-click-the-option, or correct the hidden-`<select>` + change-event path).
3. **Cut per-step latency** — Pool Name fill ~36s; the 30s unsafe-click retry firing on a *fill* field (Phase A was meant to restrict it). Profile the per-step settle/describe overhead.
4. **Re-smoke.** If green → **push the whole local stack** + propose tag `v0.5.9-vision-hybrid`.
5. **Only after the vision driver is functional:** run `dead-code-audit` again, then execute the `docs/dead-code-findings.md` cleanup sweep (delete the two backup trees + orphaned scripts/scraps/mock-exports; handle the test-coupled cluster as a group).

### Housekeeping / follow-ups (non-blocking)
- Clean nice-wing's stray Agent-1 wrong-worktree edits: `git -C ...\nice-wing-7bd008 checkout -- backend/webui_agent/`.
- Track down + fix the empty-`ANTHROPIC_API_KEY` shell hook so the launch workaround isn't needed.
- Reconcile the venv: Python **3.13** vs CLAUDE.md-locked **3.12**.
- Deferred audit defense-in-depth nits (security NIT-1/2/3 from the Phase B form-open audit).

### How to resume
Run **rooted in `loving-villani-1fe4d5`** (the 4 project skills auto-load there). Launch uvicorn with the `ANTHROPIC_API_KEY` shadow cleared + the real key injected from `.env`. Reuse the main-checkout venv. First action = step 1 above.
