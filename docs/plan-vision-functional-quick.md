# Plan — Unified Claude-Vision WebUI Path: Functional + Quick

> **Status:** drafted 2026-05-30. Goal set by Filip: ONE Claude-Vision path that is both
> **functional** (reliably resolves what the semantic-DOM path misses) and **quick**
> (low-latency / low-cost) — two qualities of the *same* path, not two tools.
> Strategic context: [[ai-first-webui-plan]] memory; origin doc `docs/plan-ai-first-webui.md`;
> live state in the `vision-stack-state` memory.

## Context

The vision-hybrid driver already exists on `feature/bootstrap` (worktree `loving-villani-1fe4d5`)
and is further along than the daily summaries imply. The act-path is **already** semantic-DOM-first
→ cached-vision-fallback → heuristic; the 30s→5s/4s timeout split is **already** done; `_kendo_select`
**already** exists. So this is NOT a from-scratch build. The real work is: (1) fix the two genuine
Kendo blockers, (2) stop the cache poison + over-eager proactive vision that make smokes slow.

The end-to-end gate is the **DHCP smoke** (intent: `Configure DHCP pool MYPOOL with network
20.20.20.0/24, default gateway 20.20.20.1`), which currently fails on the "Subnet Mask" Kendo dropdown.

## Step 0 — Worktree / branch reconciliation (do this first)

**Verified state (2026-05-30):**
- `feature/bootstrap` @ `loving-villani-1fe4d5`: HEAD `9e55fdd`, working tree **clean**, **5 commits ahead of `origin/feature/bootstrap` (`46cdbc0`), unpushed** (`438977c` relabel, `d991bb3` Phase A convergent driver, `0ffad84` Phase B open-form + Kendo, `e5bffe2` dead-code skill, `9e55fdd` docs). Vision modules present: `backend/webui_agent/vision_fallback.py`, `backend/orchestration/plan_vision_check.py`.
- `nice-wing-7bd008` (`claude/nice-wing-7bd008` ≈ `origin/main` `5889964`): **no vision code**; orphaned `vision_<hash>` stubs in `generic_driver.py`; has stray uncommitted edits. **Dead-end — do not build on it.**

**Recommendation:** do all work **in `loving-villani-1fe4d5` on `feature/bootstrap`** (only branch with the full stack + a green 764-test suite). Park nice-wing's stray edits (the 2026-05-30 doc already prescribes this):
```
git -C <nice-wing> checkout -- backend/webui_agent/ tests/unit/test_generic_driver.py tests/unit/test_playwright_subprocess.py
```
Push + tag are **Director-gated** (push only after a green smoke; tag `v0.5.9-vision-hybrid` is Filip's call — do not create it).

## Step 1 — The one-path gate (make the decision rule explicit)

Encode as a comment block at the top of `_do_act_by_intent` and at each `check_plan_via_vision` call site:

- **Reactive per-element (hot path)** — `_playwright_subprocess.py:_do_act_by_intent`: (1) DOM forward-lookup `_eid_for_intent` (≈0 cost) → (2) cached vision `vision_fallback.resolve_via_vision` (cache hit = no Anthropic call; miss = one Haiku call + cache) → (3) heuristic `first_match` → (4) surface `unknown_eid` (convergence guard aborts after 2 identical failures). `_SENSITIVE_DENY_LIST` enforced on all branches.
- **Proactive plan-vision (cost knob)** — `plan_vision_check.check_plan_via_vision`, scaled by `compute_familiarity_score`: at most once at proposal + once mid-execution (`_iter_vision_fired` guard). **Never per-step.**
- **Functional** = the reactive chain always has a vision rung catching DOM misses. **Quick** = vision is cache-gated (reactive) and familiarity-gated (proactive), so steady state is DOM-only + cache hits.

## Step 2 — Functional fixes (make it work)

### 2.1 Kendo LABEL resolution — `semantic_dom.py:describe_page` (Kendo block ~L202–214)
Root cause: `_resolve_name` runs `inner_text` (step 3) before `_spatial_label` (step 5); a Kendo `<span role="listbox">`'s `inner_text` is the *selected value* ("255.255.255.0"), so the field is named after its value and never reaches the spatial-label step.
- In the Kendo branch only, after re-classifying listbox→combobox: `name = _spatial_label(loc) or kendo_select_name or name` (spatial label wins; then the backing `select[name]`/`id`; the value only as last resort).
- **Scope to the Kendo branch** — do NOT reorder `_resolve_name` globally (regresses buttons/links/tabs; breaks `test_aria_label_beats_inner_text`).
- `_serialise` already reads the value from `select[name=...]`; leave it. Result: `name="Subnet Mask"`, `value="255.255.255.0"`, `options=[…]`. No planner change needed (`configure_planner._INNER_SYSTEM_PROMPT` rule 6 already maps combobox→`select` with a value from `options`).

### 2.2 Kendo SELECT mechanism — `_playwright_subprocess.py:_kendo_select` (L402)
Current hidden-`<select>.value` + raw `change` is what threw `unknown_error` live — Kendo's DataSource listens to its own widget events, not a raw `change` on the shadow `<select>`. Rewrite as a 3-strategy cascade (log which wins):
1. **Kendo widget API:** `evaluate` up to `.k-widget`/`.k-dropdown`, then `kendo.widgetInstance($(wrapper)).value(target); widget.trigger("change");` (guard `typeof kendo !== 'undefined'`).
2. **Real DOM via Playwright (not raw JS):** `locator.click(timeout=_ACT_TIMEOUT_FORM_MS)` to open → body-level popup `<ul role="listbox">` (often `id="<name>_listbox"`, or scope by the widget's `aria-owns`/`aria-controls`) → `page.locator("ul.k-list li.k-item", has_text=value).click(timeout=_ACT_TIMEOUT_FORM_MS)`.
3. **Hidden-select + change** (current behavior) as the final fallback for non-Kendo styled selects.

**The fix that kills `unknown_error`:** catch ONLY the JS-eval failure as `RuntimeError`; **let `PlaywrightTimeoutError` from open/item-click bubble** to `_do_act`'s non-click branch so a transient stall classifies as `element_intercepted` (retry once), not `unknown_error`. Keep `ValueError` for "value not in options".
**CLAUDE.md §4 compliance:** the popup `<li>` is a UI selection, not the router-write XHR — re-resolving/clicking it is allowed. Only the real "Apply to Device" click stays single-attempt (`click_timeout_unsafe_retry`). Add a comment saying so.

### 2.3 Vision rescues hard widgets — `vision_fallback.py:_call_haiku_vision` (L266)
With 2.1 the Kendo combobox resolves via DOM forward-lookup, so vision isn't needed in the common case. When DOM misses entirely, `_do_act_by_intent` already routes through `resolve_via_vision` → `_kendo_select`, so a vision-resolved Kendo selector benefits from 2.2 too. Tighten the prompt: "If role='combobox', return the selector for the Kendo `<span role='listbox'>` (or its backing `select[name=...]`), NOT a list item."

## Step 3 — Quick fixes (make it fast)

### 3.1 Tame proactive vision — `plan_vision_check.py:_plan_validation_signal` (L244)
Return `1.0` at `succeed_count >= 1` (today needs `>= 2`). One green run of a plan shape → Tier 0 (proactive vision skipped) → second run is ~DOM-only latency.

### 3.2 Cache hygiene — poison can't cause 30s stalls (biggest QUICK win)
- **Validate-on-load:** `vision_fallback.py:load_selector_cache` (L74) drops entries with empty/non-str selector or stale URL-hash family (folds in the manual "delete selector_cache.json before smoke").
- **Pre-trust probe:** in `_do_act_by_intent`, after a cache-HIT selector, `page.locator(selector).count()` with `_PROBE_TIMEOUT_MS` BEFORE acting; if `0` → `evict_from_selector_cache()` + re-resolve via Anthropic. **Converts a 30s action-timeout into ~200ms + one fresh vision call** (fixes the Pool-Name-36s and the `vision_526b1241` stall).
- **URL-hash fragment bug:** `_hash_page_url` (L59) drops the fragment so `#/dhcp` and `#/ospf` collide → wrong-page cache hits. Include `parsed.fragment`.
- Keep existing eviction-on-failure.

### 3.3 Restrict 30s unsafe-retry to clicks — already correct (`_do_act` L638)
The "30s on FILL" was an older single-timeout state. **Action:** add a regression test (a FILL `PlaywrightTimeoutError` yields `element_*`, never `click_timeout_unsafe_retry`).

### 3.4 Reduce vision call cost/latency
- **Image size (biggest lever):** `evidence.py:vision_screenshot` uses `full_page=True` → tall PNGs. Add `viewport_only=True` (capped width) for the *reactive* per-element call (halves encode/upload/read). Keep `full_page` for the *proactive* plan check; `scroll_into_view_if_needed()` before the reactive screenshot.
- **Anthropic timeout:** `_call_haiku_vision` / `_call_haiku_plan_vision` use SDK default + `max_retries=5` (backoff stalls). Set `timeout=20`, `max_retries=2` for vision — fail fast to default-PROCEED/heuristic.
- **Model:** keep Haiku 4.5 (locked stack); Sonnet escalation stays manual.
- **Grounding trim:** `_find_prior_screenshots` (L122) — cap at 1 prior screenshot on the first-encounter call.

## Step 4 — Touch list (all in loving-villani; no new modules)

| Concern | file:func | Change |
|---|---|---|
| Kendo label | `webui_agent/semantic_dom.py:describe_page` (~L202–214) | `name = _spatial_label(loc) or kendo_select_name or name` (combobox-scoped) |
| Kendo select | `webui_agent/_playwright_subprocess.py:_kendo_select` | 3-strategy cascade; let Playwright timeouts bubble (kills `unknown_error`) |
| Combobox vision prompt | `webui_agent/vision_fallback.py:_call_haiku_vision` | combobox clause |
| Proactive cost | `orchestration/plan_vision_check.py:_plan_validation_signal` | `>=1 → 1.0` |
| Validate-on-load | `webui_agent/vision_fallback.py:load_selector_cache` | drop malformed entries |
| Pre-trust probe | `webui_agent/_playwright_subprocess.py:_do_act_by_intent` | `count()` before trusting a cache hit; evict+re-resolve on 0 |
| URL hash fragment | `webui_agent/vision_fallback.py:_hash_page_url` | include `parsed.fragment` |
| Vision image size | `webui_agent/evidence.py:vision_screenshot` (+ callers) | `viewport_only=True` (reactive only) |
| Vision API timeout | `vision_fallback.py` / `plan_vision_check.py` Anthropic calls | `timeout=20`, `max_retries=2` |

## Step 5 — Tests (mock-locator style; no live browser)
- `test_semantic_dom.py`: `test_kendo_combobox_named_by_spatial_label_not_value` (name=="Subnet Mask", value=="255.255.255.0"); `..._falls_back_to_select_name_when_no_spatial`.
- `test_playwright_subprocess.py`: `test_kendo_select_uses_widget_api_then_falls_back`; `test_kendo_select_timeout_bubbles_as_intercepted_not_unknown_error` (locks the live failure); `test_fill_timeout_never_returns_click_unsafe_retry`; `test_cache_hit_selector_probed_before_trust`.
- `test_vision_fallback.py`: `test_load_selector_cache_drops_malformed_entries`; `test_hash_page_url_distinguishes_fragments`; cache-hit makes no Anthropic call.
- `test_plan_vision_check.py`: `test_plan_validation_signal_one_success_is_tier0` (+ adjust dependent tier tests).
- **End-to-end:** add `tests/smoke/scenarios/test_07_webui_dhcp.py` mirroring `test_06_webui_add_vlan.py`, with a teardown that deletes the pool; wire into `scripts/run_smoke_tests.py:SCENARIO_DESCRIPTIONS`.

## Step 6 — Verification

Gate 1 (required before every commit):
```
.venv\Scripts\python.exe -m pytest tests/unit -q
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m mypy backend
```
Green = 0 failures, ruff/mypy clean, count ≥ 764 + new tests.

Gate 2 — live DHCP smoke (the real gate). Launch with the key-shadow workaround:
```
Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
$env:ANTHROPIC_API_KEY = (Select-String -Path .env -Pattern '^ANTHROPIC_API_KEY=(.*)$').Matches.Groups[1].Value
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
Then drive the DHCP intent via chat (or `SMOKE_ALLOW_WRITES=1 ... scripts\run_smoke_tests.py`, headed).

**Green looks like:** Subnet Mask step shows `role="combobox", name="Subnet Mask"` (NOT "255.255.255.0"); `_kendo_select` succeeds (log `kendo_select_success`, selected == requested mask), **no `unknown_error`**; no 30s stalls; total well under the ~6-min baseline (warm re-run ~tens of seconds); `webui_verify` passes → `mark_executed`; pre-write snapshot + before/after screenshots present; approval server-enforced. After green: push (Director-gated); tag is Filip's call.

## Step 7 — Risks / open questions for the Director
- **Branch:** push `feature/bootstrap` to its remote, or PR into `develop`/`main`? (Recommend: push the branch; the live smoke is the real gate.)
- **Smoke form:** repeatable pytest `test_07_webui_dhcp` (writes+restores) vs ad-hoc chat run? (Recommend the scenario with pool-delete teardown.)
- **Model escalation:** if Haiku 4.5 still misreads the Kendo widget after the prompt tweak, auto-escalate one retry to Sonnet on confidence <0.7, or keep manual?
- **`_kendo_select` widget-API portability:** depends on the IOS-XE WebUI exposing a global `kendo`/jQuery. If not, strategy 1 skips to the Playwright click-open path (robust) — confirm `ul.k-list li.k-item` against the live DOM dump (`artifacts/screenshots/..._a88c57/99-act-error-*.html`).
- **Env (non-blocking):** empty-`ANTHROPIC_API_KEY` shell-hook shadow; venv Python 3.13 vs CLAUDE.md-locked 3.12 — reconcile before tagging.
