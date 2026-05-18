# Day summary — 2026-05-18

Short session focused on the ISIS Add modal race + design handoff. Two commits + one release tag landed. ISIS hardware retest was blocked by transient Anthropic 529 overloads — the fix is deployed but unverified end-to-end.

## Top line

2 commits on `feature/bootstrap` (tests 496 → 500). 1 release tag pushed to origin: `v0.4.0-alpha.4-settle-wait`. Design handoff doc shipped for the parallel-track redesigner.

## Commits

| Commit | Type | Summary |
|---|---|---|
| `c96b653` | fix(webui-agent) | Settle page after each action to survive modal race. New `_settle_page(page)` between every successful `_invoke_action()` and the post-action `describe_page()` inside `_do_act`. Tries `wait_for_load_state("networkidle", timeout=1500)` first (covers Cisco's chatty Angular XHR bursts), falls back to a 500ms sleep on timeout (covers pages with polling timers that never reach idle). Swallows any other exception so the describe that follows can surface real errors. Cost: ~500ms-1.5s per successful action. +4 unit tests including a `_do_act` regression that asserts settle fires on the success path exactly once. |
| `115fc2d` | docs | Design handoff brief for the redesigner — repo location, stack, current page map, component inventory for load-bearing surfaces (`ApprovalButtons.tsx`, `LiveEventStream.tsx`, `Sidebar.tsx`, etc.), the approve/execute flow step-by-step, design principles that are hard requirements (two-click HITL gate, action IDs visible, evidence one-click away, errors foregrounded, live event stream stays visible), stable-vs-in-flux table, 5 open questions back to the designer. The flow is sacred; the look is open. |

## What triggered the settle fix

ISIS Add form via WebUI failed with `inner_plan_empty` despite the alpha.3 click-Add rule shipping correctly. Saved screenshots in `artifacts/screenshots/generic_session_sess_39d94a7f/` told the story:

- Screenshot 1: modal open with the ISIS Add form visible (Router ISIS textbox, Level dropdown, Interface, Net Area + IP Address, Redistribute, Apply to Device).
- Screenshot 2: blank page — modal dismissed before `describe_page` snapshotted.

Cisco's Angular ISIS modal has a click-outside / focus-loss dismiss that fires faster than `describe_page`'s per-element `bounding_box` iteration. Static route's modal didn't have this property (different component lifecycle), which is why every flow we'd tested previously worked. ISIS surfaced the race.

`_settle_page` runs between action and re-describe so the modal is fully rendered AND stable by the time describe iterates. networkidle is the primary signal (Cisco WebUI emits XHRs after a click); the 500ms fallback covers polling-timer pages where networkidle never fires.

The modal title bug ("Add Route" displayed on the ISIS form) is unrelated — Cisco reused the static-route Add component for ISIS. Doesn't affect our resolver which matches on `{role, name}` not on the modal title.

## Test count delta

Yesterday's evening: 496 passing.

Today:
- `c96b653`: 496 → 500 (+4 settle_page tests)
- `115fc2d`: 500 (docs only)

Final: **500 passing**, 3 skipped (smoke-test hardware deps), ruff + mypy clean.

## Release tags on origin (cumulative chain)

- `v0.4.0-alpha.1-ai-configure` → `b5a88a4` — first hardware-validated cut (static route + trunk port + CLI configure)
- `v0.4.0-alpha.2-retry-guard` → `50e09c3` — per-turn propose quota + OSPF verify off `| section`
- `v0.4.0-alpha.3-add-button` → `be4e7fd` — inner planner drafts `[click Add]` when intent says add + `device_errors` surface % lines
- **`v0.4.0-alpha.4-settle-wait`** → `c96b653` — new, today

## Hardware retest blocked

After deploying alpha.4, retried the ISIS prompt. Got HTTP **529 Overloaded** from Anthropic's API:

- First attempt: failed inside `webui_configure` — the iter-2 `draft_plan` LLM call got 529 across 3 retries (SDK default).
- Second attempt: failed at the outer planner's iteration 0 — the first LLM call couldn't even start. Whole API region heavily saturated.

This is Anthropic-side, not our code. But it exposed three weaknesses in our retry posture:

1. **`Anthropic(max_retries=N)` defaults to 2** at client-construction sites. With exponential backoff that's ~2-3s of retrying. Easily overrun by an active overload event.
2. **529 propagates as `tool_failed` with raw JSON error** — user sees `Error code: 529 - {'type': 'error', 'error': {'type': 'overloaded_error', ...}}` instead of an actionable message.
3. **Mid-flow 529 kills the action** — if iter 1 succeeded but iter 2's LLM call 529s, the whole action goes to FAILED. No resumption.

Decided not to ship the retry hardening this session — would have wanted to confirm alpha.4 ACTUALLY fixed the ISIS modal race before piling on another layer. Hardening is the first chunk of next session.

## Known issues carried into next session

| Issue | Severity | Where |
|---|---|---|
| ISIS WebUI hardware retest not yet verified | high | Blocks closing the ISIS gap |
| OSPF WebUI hardware retest still pending (from 2026-05-15 backlog) | medium | alpha.3 click-Add rule unverified for OSPF |
| Anthropic 529 retry posture too thin | medium | All flows fail under transient overload |
| Router-id conflict not caught at propose time | low | alpha.3's `device_errors` surfaces it post-execute, but propose-time pre-check would be better UX |
| Consolidated `v0.4.0-alpha.1` milestone tag (no suffix) | low | Cosmetic — five alpha.* tags with suffixes exist, but CLAUDE.md §72 references the un-suffixed name |
| Design redesign in progress (parallel track) | informational | Designer has [docs/design-handoff.md](design-handoff.md); waiting on mockups |

## Architectural notes for memory carry-forward

- `_settle_page` is now load-bearing for every WebUI flow. Pattern: networkidle-with-sleep-fallback. Tunable via `_SETTLE_NETWORKIDLE_MS` and `_SETTLE_FALLBACK_MS` constants near the top of `_playwright_subprocess.py`. If a future Cisco page genuinely needs more settle time, bump those constants.
- Modal-disappearance is a class of bug, not unique to ISIS. Any Cisco page that uses focus-loss-dismisses behaviour will surface it. alpha.4 catches the immediate post-click race; a follow-up between batches in `_webui_configure`'s multi-propose loop might be needed if we see the same pattern later.
- Cisco's WebUI has a real "Add Route" component reused across protocols (ISIS, static route). Implication: our forward-eid resolver must keep matching on `{role, name}` of the form fields, NOT the modal title, since titles can be wrong.

## Design redesign — informational

Designer has [docs/design-handoff.md](design-handoff.md). Working in parallel. No mockups yet — when they arrive, next chunk in the project plan after Phase 6 is wiring them in. Until then the frontend is stable.

The five open questions in the handoff doc are blockers for committing to a final design:
1. Where does the live event stream go (right panel / bottom drawer / separate route)?
2. How prominent is the action ID (chip / expanded / hover-only)?
3. Chromium-during-execute as separate OS window (current) or embedded somehow?
4. Failed-action info hierarchy (error → device_errors → snapshots → full output)?
5. Slovak/English UI strings?
