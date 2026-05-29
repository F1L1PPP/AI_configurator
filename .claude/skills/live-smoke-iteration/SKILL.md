---
name: live-smoke-iteration
description: >-
  Ship → smoke → triage discipline for iterating against a live system (the C1111-4P router via SSH or the WebUI driver). Encodes the 2026-05-23 vision-stack saga: five load-bearing rules (visibility-first, one-smoke-one-evidence, wiring-trap-prevention, backup-tag-discipline, deep-audit-no-skipping) plus the vision-fallback architectural lessons. Invoke this from message #1 of any session that involves a live smoke result, "live router", repeated smoke failures, or a paste-bombed block of terminal / uvicorn logs.
---

# Live-smoke iteration

Iterating against the live router is where architecture meets reality. Unit tests pass and the smoke still fails — because the defect is a contract, a wiring gap, or an invisible subprocess error, not a logic bug. This skill is the discipline that keeps those loops short. Born from the 2026-05-23 vision-stack saga: 6+ hours were burned before the root cause was even *visible*.

## The five load-bearing rules

1. **Visibility-first.** If two consecutive smokes fail with the same generic symptom (`unknown_error`, `iteration_cap_hit`, silence), **STOP shipping architecture and ship the observability fix first.** The whole vision stack was invisible until 14h-C forwarded subprocess stderr (NDJSON) into the parent uvicorn log. Before that, `vision_fallback_*` events went to `DEVNULL` and every failure looked identical. ~150 lines of log-forwarding would have saved most of the wasted day. Going forward, every live-smoke iteration should surface `vision_fallback_*`, `selector_cache_evicted`, and `plan_vision_check_*` events in the parent log — read them.

2. **One smoke, one piece of evidence.** Each smoke must produce a concrete artifact that proves what happened: the specific event that fired, the exact selector returned, the `%` error line from IOS, the action_id. "It failed" is not evidence. Name the failure mode (`vision_fallback_api_error: Expecting value...`, `button:has-text('Add')` matched zero elements) before changing anything.

3. **Wiring-trap prevention.** For every new function, strategy handler, or config (YAML) you add, **grep for runtime callers before commit and count the call sites.** 14k shipped a `role_text` strategy handler *and* a `dhcp_form` YAML — both dead code, never wired into the runtime path. Tests caught nothing; it burned a router smoke. Dead code that passes tests is still dead.

4. **Backup-tag discipline.** Tag a `backup-YYYYMMDD-HHMM` safety net at mid-day and end-of-day on a live-iteration day (e.g. `backup-20260523-1259` mid-day, `backup-20260523-1842` end-of-day). These are informational, never moved, never release tags. They let you revert a wrong architectural turn (like the 14g inversion) without losing the day.

5. **Deep-audit, no skipping.** Never skip the Opus 4.8 deep audit on a smoke-touching chunk, no matter how "small" the surface looks (`director-blueprint` tier rule). The 14k dead-code burn happened *because* the audit was skipped on "small surface, exactly what was recommended."

## Architectural lessons from the vision stack

- **Vision-from-screenshot fundamentally cannot see HTML attributes.** Ask Haiku for an attribute selector (`input[name='networkIp']`) and it falls back to what it can *see* — text selectors (`button:has-text('Add')`). On Cisco's hostile DOM (icon-only buttons, `<span><i></i>Add</span>` nesting, no proper labels) those match zero elements. Vision needs DOM context to emit attribute selectors.
- **Hybrid beats pure-vision-first.** The correct selector-resolution order is **eid forward-lookup FIRST** (the describe view *does* carry HTML attribute knowledge) **→ vision fallback** (only for elements describe dropped) **→ first_match heuristics** (last resort). 14g's vision-first inversion was wrong — it skipped the correct eid path for describable elements like the Add button. 14h-F reverted to the hybrid.
- **Cache hygiene needs catch-all eviction.** Include the Playwright catch-all `unknown_error` in the staleness set so a poisoned cached selector self-heals on the next failure. Over-evict, don't under-evict. (Had to delete a poisoned `selector_cache.json` once.)
- **Default-PROCEED on pre-check failure paths.** A vision pre-check must never hard-fail on an API hiccup (timeout, 529, JSON parse error). The action store + operator approval flow is the safety net — not the pre-check.
- **Familiarity scaling must filter to EXECUTED-only signals.** 10 failed retries leaving forensic snapshots must not inflate "familiarity." Cross-reference `snapshot_signal` against `webui_configure_iteration_complete` events with `verify_present=true`.
- **Option H — trust the LLM's suggestion when it gives one.** When vision REJECTs a plan but returns a `suggested_plan`, promote to REVISE and use it. The model saw the form; its suggestion is authoritative.
- **Recover JSON from prose.** Both `plan_vision_check` and `vision_fallback` had to brace-extract JSON from Haiku prose responses (`json.loads(raw_text)` directly fails on empty/prose).

## Worked example — the 4+ DHCP smokes (same intent, evolving failure modes)

| Smoke | After fix | What it proved |
|---|---|---|
| 1 | pre-14h-C | Vision-first fired but **silently** failed (stderr discarded) → fell back to heuristics → picked a column-header link → `iteration_cap_hit`. Couldn't even tell vision was broken. |
| 2 | 14h-C log forwarding | First visibility: `vision_fallback_api_error: Expecting value: line 1 column 1` — Haiku returning empty/prose. → fixed JSON recovery. |
| 3 | 14h-D uniqueness prompt + 90s timeout | Vision returned `button:has-text('Add')`; click failed because the button's text is in nested children. Eviction + retry returned the **same** selector. |
| 4 | 14h-E cache eviction | Cache evicted correctly (`selector_cache_evicted` visible — 14h-C working), but vision **still** returned `button:has-text('Add')` → confirmed the can't-see-attributes constraint. |
| pending | 14h-F hybrid revert | Should bypass vision entirely for Add (`e_020` is in the describe view; eid forward-lookup finds it). Vision only fires for fields not in describe. **Smoke this first.** |

## When the Director paste-bombs terminal logs
That is a smoke result. Parse it for: the action_id, the resolved selector, which `vision_fallback_*` / `selector_cache_*` events fired (or didn't), and the IOS `%` line. Lead with the named failure mode, then the one-line root cause, then the fix. Don't restate the whole log back.

## Reference
- `director-blueprint` — role split, the audit-tier rule this leans on, the bug-fix loop.
- `docs/today-2026-05-23-summary.md` — the full saga (commit chain `b8ef295`→`79dc895`, all lessons).
- `CLAUDE.md` "Before every router write" — snapshot → approval → screenshots → never auto-retry.
