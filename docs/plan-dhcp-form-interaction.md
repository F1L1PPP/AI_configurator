# Plan — DHCP WebUI Form Interaction (v2, post 15-agent burst)

> **Status:** revised 2026-05-31 after a 15× Opus parallel investigation. The Kendo-dropdown
> blocker is FIXED. A first pass of form-interaction fixes is committed (WIP, pre-smoke). This
> doc now reflects the **corrected root cause** (duplicate Add/Edit windows) and the real path
> to a green smoke, plus high-value safety/bug findings from the audit swarm.
> Push stays gated on a green smoke (Director's rule). Unit state: 800 pass, ruff + mypy clean.

## Corrected root cause — DUPLICATE form windows (the real blocker)
The IOS-XE DHCP page renders **both the "Add" and "Edit" Kendo windows in the DOM at once**, so
the form fields are **duplicated**: 4× `<select name="ipTypeList">`, 2× each of
`networkIp` / `startingIp` / `endingIp` / `subnetmaskOptions` / `scopeName`. Consequences:
- IP Type "select" resolved to the **wrong** combobox (`e_013` "Reserved Only") → `unknown_error`.
- `field_key` / `name` resolution can't disambiguate duplicates; `select[name='ipTypeList']` hits
  Playwright strict-mode (multi-match).
This — not "Apply not surfaced" — is why the run churns. Three agents converged on it independently.

Two earlier premises were wrong:
- **"Apply to Device" is a real `<button>`**, not a styled span. It's dropped by the **30-element
  cap** (bottom of a tall modal, low centrality score). Fix = protect form-action buttons from the cap.
- **`default gateway` has NO field in the Basic form** — `routers`/`routerIp` is `ng-show="mode=='advance'"`
  (Advanced only). The planner literally cannot place 20.20.20.1 in Basic mode.

## Already implemented (committed `5f19144`, WIP — needs window-scoping + deep audit)
- `semantic_dom`: `field_key` (name/ng-model tail) on every element; `_is_apply_control` + score
  boost so Apply-to-Device survives the cap.
- `_playwright_subprocess`: `_eid_for_intent` bridges intent→`field_key` on exact-match miss;
  submit/apply intents refuse to resolve onto Cancel/Close.
- These help but are **insufficient alone** — duplicates defeat field_key too.

## Ordered fix sequence to a GREEN smoke
1. **Scope `describe_page` to the VISIBLE Kendo window** (`semantic_dom.py:describe_page`,
   `_resolve_kendo_select_name`, the `select[name=...]` reads in `_serialise`). De-duplicate the
   view so only the on-screen Add window's ~10 fields are surfaced. **Highest leverage; everything
   else depends on it.** GUARDRAIL: gate on "a visible Kendo window exists, else describe whole
   page" so VLAN/hostname (non-Kendo) forms don't regress to an empty view.
2. **Disambiguate backing-select lookups** — scope `select[name=...]` to the widget's own ancestor
   (`xpath=ancestor::*[contains(@class,'k-widget')]//select`) or `.first`, never page-global.
   (`_serialise`, `_kendo_select` strategy 2/3.)
3. **Skip already-correct fields** — IP Type already = IPV4, Subnet Mask already = 255.255.255.0.
   Have `_kendo_select` early-return on an already-selected value, OR tell the inner planner not to
   re-select a combobox whose `value` already matches. Removes the two steps that kill the run.
4. **Stop the 90s session-op timeout from killing the session** (`_subprocess.py:_SESSION_OP_TIMEOUT_S`).
   A single slow field (vision churn) currently overruns 90s → child killed → `no live session`.
   Add a per-field wall-clock guard (~35-45s) inside `_do_act_by_intent` that returns a *recoverable*
   soft-failure instead of a hard session kill; keep 90s as a backstop. (Also: cut reactive vision
   cost — `_VISION_TIMEOUT_S` 20→8, `_MAX_RETRIES` 2→1.)
5. **Cancel/close deny** — committed; keep as defense-in-depth.

Each chunk: mock unit tests → **deep audit (Opus, shared-by-all-forms)** → re-run VLAN/hostname unit
tests → live re-smoke.

## Smoke intent change
For a **first** green: drop the gateway clause (a pool + network/mask + range is a valid, verifiable
pool). Intent: `Configure DHCP pool MYPOOL with network 20.20.20.0/24` (range optional).
Add Advanced-mode + `default-router` as a follow-up. CLI verify (the real gate):
`show running-config | section ip dhcp pool MYPOOL` → must contain `network 20.20.20.0 255.255.255.0`.
Teardown: `no ip dhcp pool MYPOOL`. (No `test_07` scenario exists yet — ad-hoc chat run for now.)

## High-value findings from the audit swarm (act before push)
**SECURITY (CLAUDE.md §4 — flag to Director):**
- The generic `webui_configure` write path takes **NO pre-write device snapshot** — the hand-coded
  VLAN/hostname flows do, but the AI-driven path does not. §4 requires it. Add `take_snapshot(action_id,"pre")`
  in `_webui_configure` before the first act.
- **Executed steps are not bounded by the approved plan** — `_webui_configure` re-drafts each iteration
  from (attacker-influenceable) page content and only *logs* when it exceeds the approved step count.
  The deny-list is the sole content guard, and it is **blind to icon-only controls** (Cisco's save/delete
  glyphs have no accessible name) and omits erase/shutdown/remove/default. Bound execution to the approved
  plan and/or allow-list the approved submit control.

**Correctness / robustness bugs:**
- Vision pre-trust probe accepts a **unique-but-WRONG** selector (`count()>0`, not name/role-verified) —
  this is exactly the hallucinated `aria-label='Network'` failure. Add a name/role cross-check; require
  `count()==1 :visible`; probe fresh selectors too (currently only cache hits are probed).
- `check_plan_via_vision` crashes the planner on **non-dict JSON / non-numeric confidence** (no isinstance
  guard) — same class as the cache fix we already shipped. 1-line guards.
- `_kendo_select` strategy-2 `has_text` is a **substring** match (can pick the wrong option) and the
  `aria-expanded` guard reads the wrong node (can toggle the popup shut on retry).
- The `no_progress` guard can't see a **cycle** (varied failures) — add a post-iteration **view-fingerprint
  oscillation** detector + session-resurrection-on-death.

**Test gap:** every JS `evaluate` site is mock-stubbed, so the destructuring bug (and these) can only
surface live. Add a hermetic **headless-Chromium fixture** tier (`file://` static DHCP form, no router)
— one fixture + test file covers 5 blind JS sites in ~1-2s. Replace the `inspect.getsource` regression
lock with a real-binding test.

## Research (closing the "fast like Chrome" gap)
Production agents (browser-use, Stagehand, Prune4Web, WebVoyager) confirm our DOM-first choice: the W3C
accessible-name algorithm **ignores `name`/`ng-model`**, so an a11y-tree agent is blind to exactly these
forms. Adopt: (1) a name/ng-model-aware scoring cascade (keywords, not selectors → no hallucination),
(2) observe-then-act (model picks from an enumerated candidate set, never authors a selector),
(3) cache resolved actions gated on confirmed submit-success, (4) active loop-breaker (dedup, not just a
step cap), (5) MutationObserver for submit-success / form-reset / blur-validation. Kendo: drive via
`$(el).data("kendoDropDownList").value(x).trigger("change")`.

## Committed so far (unpushed, green-smoke-gated) — 14 ahead of origin
Kendo chunk 1 · cache/vision chunk 2 · evaluate-sig fix · cap revert · scope doc · **mypy fix** ·
**cap-test speed** · **form-interaction WIP**. 800 unit tests green, ruff + mypy clean.
