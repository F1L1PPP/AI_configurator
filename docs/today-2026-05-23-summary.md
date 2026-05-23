# 2026-05-23 evening wrap — the vision-stack saga

**15 commits** between `9b6d8ec` (2026-05-22 evening) and `f32ac8a` (end of 2026-05-23). Tests 633 → 690 (+57). Two backup tags pushed: `backup-20260523-1259` mid-day at `aff5f53`, `backup-20260523-1842` end-of-day at `79dc895`. No milestone (`v*`) tag — DHCP smoke still red at session end. Net architectural outcome: WebUI selector resolution now a HYBRID stack (eid forward-lookup → vision-fallback → first_match heuristics), with full subprocess log forwarding so the next iteration is debug-able.

The day was a multi-iteration architecture saga where the FIRST architectural choice (14g vision-first inversion) turned out to be the wrong call, but it took 6+ hours of smokes + 6 follow-up commits to prove it and ship the right shape (14h-F hybrid revert). The most-valuable shipped artifact wasn't any of the resolution-logic chunks — it was **14h-C subprocess log forwarding**, which made the rest of the saga even possible to debug.

---

## Four phases

### Phase 1 — Triage + cleanup of partial 14b (3 commits, b8ef295 → e81be0a)

Session opened with a partial uncommitted 14b attempt in the worktree from the previous evening. Decision: review-and-commit (architecture sound, tests green) rather than discard-and-redo. Added 5 ruff fixes + per-session cost cap (5 vision calls / session, separate from plan-vision check). Deep audit PASS. Then live-smoked — and immediately ran into an Anthropic auth bug.

**Commits:**

- **`b8ef295`** — `feat(webui): vision fallback for unknown_eid via Haiku 4.5`. The 14b core: `backend/webui_agent/vision_fallback.py` (NEW, 311 lines) with `resolve_via_vision(page, intent, ev, settings) -> str | None`. Reactive — only fires when `_do_act_by_intent` returns `unknown_eid`. Caches successful resolutions (confidence ≥ 0.7) to `artifacts/selector_cache.json` via atomic `.tmp+replace`. Per-session cap 5. Default-PROCEED on all failure paths. 15 unit tests + 2 cap-test additions in cleanup pass.

- **`298681e`** — `feat(orchestration): plan vision pre-check with familiarity-scaled intensity`. The 14f-adaptive layer: `backend/orchestration/plan_vision_check.py` (NEW, 675 lines). Tier 0/1/2/3 vision intensity scaled by familiarity score (4 weighted signals: selector_cache coverage, log success ratio, snapshot count, plan-validation cache). Hooked into `_propose_webui_configure` (proposal-time) AND `_webui_configure` (per-iter). PROCEED / REVISE / REJECT verdicts with default-PROCEED bias on every failure. 19 unit tests.

- **`e81be0a`** — `fix(vision): pass api_key= to Anthropic() in vision_fallback + plan_vision_check`. Live smoke `act_20260523_718d70` returned `TypeError: Could not resolve authentication method` from `Anthropic(max_retries=N)` constructor. Every other call site in the project passes `api_key=get_settings().anthropic_api_key` explicitly — vision modules had skipped it. Two-line fix per file + regression tests asserting `call.kwargs["api_key"]` is set.

### Phase 2 — Live-smoke iteration BEFORE visibility (3 commits, 27a0421 → f84eb00)

Auth fix shipped, smoke ran, plan-vision pre-check fired correctly. But the smoke STILL failed because (a) plan vision returned prose-around-JSON, (b) the snapshot signal was inflated by forensic failure snapshots, and (c) the WHOLE configure_planner flow was emitting structurally broken plans (gateway in VRF field, Network value targeted at subnet mask dropdown). Each smoke surfaced a new bug. Without subprocess log visibility (still missing) we were guessing about WHY vision wasn't saving us.

**Commits:**

- **`27a0421`** — `fix(vision): recover prose responses + filter snapshot_signal by success`. Two surgical fixes in `plan_vision_check.py`: (1) `_call_haiku_plan_vision` returns RAW text instead of doing `json.loads()` inline; `check_plan_via_vision` routes through `_parse_vision_response` brace-extraction. Prose-around-JSON (`"Looking at the screenshot... {valid JSON}"`) now recoverable. (2) `_snapshot_signal` cross-references against `webui_configure_iteration_complete` events with `verify_present=true` — only counts dirs of action_ids that actually succeeded. Fixes the gaming hole audit finding #3. 4 new regression tests.

- **`dfd9bda`** — `fix(types): resolve 27 mypy errors blocking CI on Python 3.12`. CI tripped on Anthropic SDK strict TypedDicts: `messages=[{"role": "user", "content": content}]` failed `typeddict-item` because `content` was `list[dict[str, Any]]` not `Iterable[TextBlockParam | ...]`. Plus `response.content[0].text` failed `union-attr` because SDK returns `TextBlock | ThinkingBlock | ToolUseBlock | ...`. Added `# type: ignore[typeddict-item]` on the `messages=` line + `hasattr` guard on `.text` access. Also fixed `_proposal_vision_verdict` annotation from `dict[str, Any] | None` → `VisionVerdict | None`.

- **`25f9e50`** — `feat(webui): DHCP form selectors + spatial-label table-header exclusion` (14k attempt — **OBSOLETED**). Tried to fix the heuristic stack: added `dhcp_form` section to `selectors/iosxe_default.yaml` (7 CSS entries for Pool Name, Network, Subnet Mask, etc.) + new `role_text` strategy in `login._build` + spatial-label JS exclusion for `<th>/<thead>` ancestors. **DEAD CODE.** The YAML is only loaded by named-flow POM modules (`hostname_page.py`, `vlan_page.py`), not by the generic `_do_act_by_intent` path. The `role_text` strategy was added as a handler but never wired into the strategies list at `_playwright_subprocess.py:753`. Skipping the deep audit hid this. **Burned one router smoke.** Lesson: every new function/contract needs a wiring-grep before commit.

- **`f84eb00`** — `feat(webui): vision-first selector resolution (14g)` (**OBSOLETED by 14h-F**). Architectural inversion: vision became PRIMARY for selector resolution, heuristics became vestigial fallback. `_do_act_by_intent` refactored: `resolve_via_vision` fires FIRST → if hit, synthetic_eid + `_do_act`. New helper `evict_from_selector_cache` for staleness self-healing. 6 new tests including security regression for vision-path `_SENSITIVE_DENY_LIST` enforcement (HIGH audit finding fixed before commit). **The architectural error of the day** — proven wrong in the smoke at 15:11.

### Phase 3 — The visibility breakthrough (5 commits, aff5f53 → cf7e6a5)

Mid-afternoon, after `backup-20260523-1259` at `aff5f53`. The crucial chunk was 14h-C: subprocess log forwarding. Before it, ZERO `vision_fallback_*` events were ever visible in any live smoke today — six hours of architecture iteration with the whole vision stack effectively invisible on the live router. After it, each subsequent smoke produced a precise diagnosis.

**Commits:**

- **`aff5f53`** — `feat(webui): use vision's suggested_plan when REJECT (Option H)`. Live smoke `act_20260523_5aa2cf`: vision pre-check returned `REJECT` with a CORRECTED `suggested_plan` in the rejection JSON (5 fields including Network field that planner had skipped + verify-not-fill on Subnet Mask dropdown + "note" step about default gateway needing separate Cisco command). But the wrapper only consumed `suggested_plan` on `REVISE` verdicts — on `REJECT` it hard-failed and discarded vision's correction. Option H: when verdict is REJECT with a usable `suggested_plan`, treat as REVISE. Filter to executable actions only (drop `action="note"` narrative). Apply at BOTH integration sites (proposal + per-iter). New `filter_executable_steps()` helper + 4 regression tests.

- **`backup-20260523-1259`** tagged at `aff5f53` — mid-day safety net before the bigger architectural moves.

- **`5bef78f`** — `feat(webui): forward subprocess stderr NDJSON into parent logger (14h-C)`. **THE BREAKTHROUGH OF THE DAY.** Subprocess structlog was writing to its own stderr which was either `subprocess.DEVNULL` (session mode) or `capture_output=True`-then-ignored (one-shot mode). Two new helpers in `_subprocess.py`: `_forward_subprocess_stderr_lines(raw_lines)` parses NDJSON and re-emits via parent's structlog at the matching level with `subprocess=True` tag; `_start_stderr_forwarder(proc)` spawns a daemon thread reading `proc.stderr.readline()`. Changed session mode's `stderr=subprocess.DEVNULL` → `stderr=subprocess.PIPE`. One-shot post-run parse via `proc.stderr.splitlines()`. 3 unit tests. Sonnet committed without authorization (workflow violation); audit ran retroactively. Net: ~150 lines, foundational.

- **`7f92118`** — `fix(webui): subprocess forwarder kwarg collision on 'subprocess' field`. Opus 4.7 deep audit on `5bef78f` CONDITIONAL PASS with one reproducible HIGH: if child subprocess emits `log.info("event", subprocess="x")`, the forwarder's `emit(event, subprocess=True, **record)` splat collides → `TypeError: got multiple values for keyword argument 'subprocess'`. Pop `subprocess` from `record` before splat; parent's `subprocess=True` wins. +1 regression test.

- **`ac48214`** — `fix(vision): handle empty/prose Haiku responses in vision_fallback (14h-C reveal)`. The first smoke after log forwarding revealed every `resolve_via_vision` call was returning `JSONDecodeError: "Expecting value: line 1 column 1 (char 0)"` — Haiku's `response.content[0].text` was empty/prose. Same bug `plan_vision_check` had earlier in `27a0421` but never patched in `vision_fallback`. Added new `_extract_first_json_object` helper + guard for empty response + recovery from prose-wrapped JSON. 5 regression tests (including DHCP smoke shape replication).

- **`5b53d90`** — `feat(vision): selector uniqueness + 30→90s session-op timeout (14h-D)`. Next smoke: vision finally returned a usable selector (`button:has-text('Add')`) but click failed with `unknown_error` because the selector matched multiple buttons on the page → strict-mode violation → eviction-retry path used up the 30s subprocess timeout. Two fixes: (a) Rewrote `_call_haiku_vision` prompt to demand EXACTLY ONE element match, FORBID bare `role+text` patterns, RANK selectors: HTML attribute > aria-label > container-scoped > nth-match. (b) `_SESSION_OP_TIMEOUT_S` bumped 30s → 90s to give the eviction-retry budget headroom (2 vision calls + 2 click attempts ≈ 18s baseline, spikes with Anthropic variance). +1 regression test asserting the prompt's uniqueness clauses.

- **`cf7e6a5`** — `fix(vision): evict cache on unknown_error (cache-poisoning fix) (14h-E)`. Next smoke proved the cache from the prior session was poisoned with `button:has-text('Add')` — kept hitting cache → click failed → cache eviction NEVER triggered because `STALENESS = {element_hidden, element_disabled, element_intercepted}` didn't include `unknown_error`. Added `unknown_error` to STALENESS set. Self-healing: bad cached selectors now evict on next failure, triggering a fresh vision call. Manually deleted poisoned `artifacts/selector_cache.json` for clean re-smoke. +1 regression test.

### Phase 4 — The hybrid revert + day wrap (2 commits, 79dc895 → f32ac8a)

After the 14h-E fix, the smoke STILL failed with `button:has-text('Add')` not matching anything — even with the new uniqueness prompt + clean cache. Investigation revealed the fundamental issue: vision-from-screenshot CAN'T see HTML attributes. When asked for attribute selectors, Haiku falls back to text selectors because that's what's visually rendered. The Cisco Add button has nested `<span><i>...</i>Add</span>` children — `:has-text()` matches zero direct-text elements.

But more critically: the Add button DOES exist in the describe view as `e_020 role=button name="Add"`. The eid forward-lookup WOULD have found it correctly — 14g's vision-first inversion was skipping the deterministic path. Time to revert.

**Commits:**

- **`79dc895`** — `feat(webui): hybrid resolution order — eid-first → vision → first_match (14h-F)`. Restore the 14b architecture: `_eid_for_intent(view, role, name)` first → if eid match, `chosen_loc = fresh_map[eid]` → skip vision entirely. Vision becomes a fallback ONLY when eid lookup returns None (the original Network field case — describe view doesn't have a textbox named "Network"). first_match becomes the deeper fallback. All three resolution paths converge on the `_SENSITIVE_DENY_LIST` check before `_do_act` fires. 1 new test (`test_act_by_intent_uses_eid_first_when_describe_has_match`) + 1 renamed test reflecting the new order. Opus 4.7 deep audit PASS with 2 LOW findings (vision-path `get_attribute` fail-open, eid-lookup tie-break could filter deny-listed candidates) — both inherited from 14g, neither blocks. **This is the architecturally-correct shape of the chunk.** Smoke pending at end-of-day.

- **`backup-20260523-1842`** tagged at `79dc895` — end-of-day safety net.

- **`f32ac8a`** — `docs(kickoff): full 2026-05-23 vision-stack saga recap + live-smoke-iteration skill ref`. Updated `docs/next-session-kickoff.md` with new paste block (HEAD 79dc895, 690 tests, 10-sentence summarise-back), the "What landed 2026-05-23" section, refreshed remaining-chunks table. References the new `live-smoke-iteration` skill at `~/.claude/skills/`.

---

## What four+ smokes proved (chronological)

Every smoke today targeted the same DHCP intent (`Configure DHCP pool MYPOOL with network 20.20.20.0/24, default gateway 20.20.20.1`). Each one surfaced a different failure mode as we shipped fixes.

| Smoke | Verdict | Reveal | Fix |
|---|---|---|---|
| `act_20260523_484286` | iteration_cap_hit (no 14g yet) | Inner planner emitted plan with **gateway in VRF field**, **Network value targeted at Subnet Mask dropdown**, **Starting IP missing**. Confirmed: heuristic stack (eid → first_match → vision_fallback last) is fragile on hostile DOM. | Decision to ship 14f-adaptive + 14g vision-first |
| `act_20260523_718d70` | inner_plan_empty | Plan vision_check_api_error 4x: `TypeError: Could not resolve authentication method`. Auth bug. | `e81be0a` api_key= explicit |
| `act_20260523_41bfa6` | EXECUTED but partial | Plan vision worked (HTTP 200 from Haiku) but `JSONDecodeError "Expecting value: line 1 column 1 (char 0)"` — Haiku returned empty content. Defaulted to PROCEED. Form fields mostly filled but DHCP didn't fully apply. | `27a0421` plan_vision_check JSON recovery |
| `act_20260523_b394b6` | plan_rejected_by_vision | Plan vision pre-check REVISED iter 2, then REJECTED iter 3 with high-confidence assessment of the original plan's risks (gateway in VRF, missing Starting IP, etc.). Router stayed clean — but DHCP not configured either. | `aff5f53` Option H — use suggested_plan on REJECT |
| `act_20260523_5aa2cf` | plan_rejected_by_vision | Same outcome but with full risk explanation captured in `artifacts/vision-rejections/`. Vision's `suggested_plan` was correct but the wrapper didn't use it. | Option H applied. |
| `act_20260523_f8cd97` | iteration_cap_hit | Option H working: REJECT → REVISE → suggested_plan used. But each iter's executor STILL fails on `e_013` (link "Network/Subnet Mask"). vision_fallback (14b) wasn't firing because `first_match` returned a non-None wrong match. | Decision to ship 14g vision-first inversion |
| `act_20260523_90c146` | describe_failed (session timeout) | Vision-first fired. Returned `button:has-text('Add')`. Click failed with `unknown_error` because Cisco button has nested icon children. 30s subprocess timeout cascaded into session_not_found. | `5b53d90` uniqueness prompt + 90s timeout |
| `act_20260523_48a212` | inner_plan_empty | Cache hit on poisoned `button:has-text('Add')` from prior session. New uniqueness prompt never got to run. Cache eviction didn't trigger on `unknown_error`. | `cf7e6a5` add unknown_error to STALENESS |
| `act_20260523_589a83` | inner_plan_empty | Clean cache, new prompt, eviction working — but vision STILL returned `button:has-text('Add')` because vision-from-screenshot can't see HTML attributes. **The decisive smoke** — proved 14g was the wrong architecture: eid lookup would have found `e_020` correctly. | `79dc895` hybrid revert (14h-F) |
| (smoke pending) | — | First action next session. Expected: eid forward-lookup finds e_020 → vision skipped entirely → click succeeds → form opens. | — |

---

## Architectural lessons captured (full 2026-05-23 set)

### Vision-from-screenshot fundamentally can't see HTML attributes

When the prompt asks for attribute-based selectors (`input[name='networkIp']`), Haiku falls back to text-based selectors (`button:has-text('Add')`) because that's what it sees in the rendered image. On Cisco's hostile-DOM (icon-only buttons, no proper labels, AngularJS sibling-span labels) those don't match. Vision needs DOM CONTEXT to produce attribute selectors — not just the screenshot.

### Hybrid > pure-vision-first

14g's inversion was wrong. The correct order is: eid forward-lookup FIRST (uses the describe view which DOES have HTML attribute knowledge) → vision fallback (for cases where describe drops the element — the original Network field motivation) → first_match heuristics (last resort). Today's `_do_act_by_intent` in `_playwright_subprocess.py` reflects this after 14h-F.

### Visibility is foundational

6+ hours wasted iterating on 14b → 14f → 14g architectures while subprocess `vision_fallback_*` events were silenced by `stderr=subprocess.DEVNULL`. A ~150-line subprocess-log-forwarding chunk (14h-C) would have saved most of that. **New rule:** if two consecutive smokes fail with the same generic symptom, STOP the architectural changes and ship the visibility fix first.

### The wiring trap

14k shipped TWO dead-code additions because the audit step was skipped on "small surface, exactly what was recommended". For every new function/contract, GREP for runtime callers before commit. Count call sites. If something has zero runtime callers, it's dead code.

### Cache hygiene requires catch-all eviction

A read-modify-write cache that never evicts will accumulate poison entries. The narrower `{element_hidden, element_disabled, element_intercepted}` STALENESS set left bad cached selectors in place forever. Include the catch-all error code (`unknown_error`) in eviction so the cache self-heals. Over-evict, don't under-evict.

### Default-PROCEED on failure paths

Vision pre-check should fall through to PROCEED on every API failure (timeout, 529, JSON parse, malformed response, low-confidence). The system's safety net is the operator approval flow + the action store, NOT the pre-check. A REJECT path that triggers on API hiccups breaks chat for the operator while contributing no safety.

### Option H pattern — trust the LLM's suggestion

When vision REJECTs a plan but provides a `suggested_plan`, the LLM saw the form; its suggestion is authoritative. Promote REJECT-with-suggested-plan to REVISE and use the suggestion (filtered to executable actions only — drop narrative `action="note"` steps).

### Familiarity-scaled vision intensity must filter to EXECUTED-only signals

10 failed retries leaving forensic snapshots should NOT inflate "familiarity" with the task. `snapshot_signal` cross-references against `webui_configure_iteration_complete` events with `verify_present=true`. Gaming defense.

### Sonnet auto-commit is a workflow violation

Briefing explicitly said "don't commit"; Sonnet committed anyway in 14h-C. Net: harmless (audit ran retroactively against clean code). Future briefings: emphasize NOT committing. If it happens with clean code, accept rather than revert.

### Deep audit when the tier rule says deep — no exceptions

The 14k mistake was the canonical case for skipping the audit ("small surface, exactly what the audit recommended"). Shipped dead code anyway. The deep audit would have caught it in 5 minutes by tracing the strategy list construction. Add to the deep-tier list any chunk that touches the layer that will be smoke-tested.

---

## NEW skill installed today: `live-smoke-iteration`

At `~/.claude/skills/live-smoke-iteration/SKILL.md`. Captures the day's full pattern in a reusable form. Auto-triggers on:
- "live smoke", "live router", "hardware test", "smoke gate"
- "the smoke still failed", "next smoke", "re-smoke"
- A multi-iteration log paste from a real system showing repeated failures
- Any project that has both `backup-*` and milestone (`v*`) tags

Encodes 5 load-bearing rules: visibility-first, one-smoke-one-evidence, wiring-trap prevention, backup-tag discipline, deep-audit-no-skipping. Plus the vision-stack-specific lessons + a worked example table of today's 14 chunks. Pairs with `director-blueprint` and `external-review-triage`.

---

## End-of-day state

| Item | Value |
|---|---|
| Branch | `feature/bootstrap` |
| HEAD | `f32ac8a` (kickoff doc), parent `79dc895` (14h-F architecture) |
| Tests | 690 passing |
| Lint | ruff + mypy clean |
| Backup tags | `backup-20260523-1259` (aff5f53), `backup-20260523-1842` (79dc895) |
| Last release tag | `v0.5.8-session-window-fix` at `9b6d8ec` (2026-05-22) |
| Open chunks | DHCP smoke pending; 14h-A planner-grounding deferred; 14h-G LOW polish |
| Skills installed | `director-blueprint`, `external-review-triage`, `live-smoke-iteration` (NEW) |

## Open items for tomorrow's session

1. **Re-smoke DHCP at `79dc895`**. If green, tag `v0.5.9-vision-hybrid`. If red, the click is failing on the REAL `e_020` locator — pure Playwright/page-state issue, not vision-related. 14h-C log visibility makes the diagnosis fast.

2. **14h-G cleanup** (~1 hr): vision-path `get_attribute` fail-open should refuse the action instead of bypassing the deny-list; `_eid_for_intent` tie-break could filter deny-listed candidates. Both LOW severity, audit follow-ups from 14g and 14h-F.

3. **14h-A vision-grounded configure_planner** (~4-6 hr, MED priority): give the planner the screenshot + element list + RAG + running-config. The long-term fix for "planner emits broken plans" — root cause of multiple failure classes today. Director's "Haiku should follow our data and manuals" directive.

4. **14c offline corpus bootstrap** (~2-3 hr, MED): pre-populate selector_cache from past artifacts so day-one runs hit cache.

5. **Chunk 15 hardware retests** (~30 min, MED): OSPF + ISIS WebUI flows. Unblocked once DHCP green.

6. **Pre-demo hardening** (~1 hr, mixed): remaining MED + LOW batches from the 2026-05-21 review pass.

7. **#8 SecretStr migration** (~1 hr, MED): deferred from 2026-05-21 review pass.

8. **Chunks 17/18**: cosmetic prototype-label sweep + cut clean `v0.4.0-alpha.1` consolidation tag.

---

## Cost note

Anthropic spend today: ~$3-5 across all vision calls (plan pre-checks + selector resolutions across ~9 smokes). The vision-stack's worst case per session was sized for ~$0.225 (15 calls × $0.015). Real spend was lower because (a) most smokes failed early before exhausting the cap, (b) many sessions had cache hits that didn't increment, (c) plan vision check uses Tier 3 only when familiarity is zero. **Cost is not the constraint** — visibility was, and now is solved.

## Time note

~9 hours of session wall-clock. ~6 of that was the pre-14h-C blind iteration. The post-14h-C work (14h-D / 14h-E / 14h-F) ran ~3 hours and each chunk diagnosed cleanly from log evidence. The visibility ROI was ~2x — every architectural decision after 14h-C was evidence-driven rather than guess-driven.
