# Day summary — 2026-05-15

## Top line

12 commits on `feature/bootstrap` (tests 381 → 444). All landed cleanly; backup tag `backup-20260515-1031` pushed to origin. The day shipped: 5 security quick wins, Phase 3.3 + 3.4 (name resolution), Phase 5 (`propose_webui_configure` + `webui_configure`), an autonomous + a human-driven catalog walker, nav-map injection, a runaway-fix bundle, and the production-LLM rule (Haiku-only).

**Known gap at backup**: Phase 5 static-route flow produces a single-step plan only (just `click Add`) — multi-step planning across page transitions is the next architectural unit of work. Recommended next chunk in [next-session-kickoff.md](next-session-kickoff.md).

## Commits

| Commit | Type | Summary |
|---|---|---|
| `e2bceac` | docs | Annotate timing estimates on quick-win subsections in kickoff |
| `441150c` | fix(security) | Pre-Phase-5 hardening — sanitizer + `<doc_chunk>` + deny-list + URL-origin guard + docs alignment |
| `842a316` | feat(webui-agent) | Phase 3.3 — `_resolve_name` extended with `<label for>` / title / name / id (skip `ng-*`) |
| `07025bb` | feat(orchestration) | Phase 5 — `propose_webui_configure(intent)` + `webui_configure(action_id)` |
| `2288b64` | feat(scripts) | Sidebar-driven auto-discovery in `catalog_webui_elements.py` |
| `1e72aed` | feat(scripts) | New `record_webui_catalog.py` — human-driven recorder |
| `4c8d1d5` | fix(scripts) | Recorder reads URL via `evaluate()` instead of stale `page.url` (hash-route fix) |
| `fa32c74` | fix(catalog) | Recorder save survives cleanup error + salvage 32-page nav map |
| `6329e96` | feat(orchestration) | Phase 5 Sub-task B — nav-map injection into outer SYSTEM_PROMPT |
| `d8d1367` | fix(orchestration) | Phase 5 Sub-task C — tighten inner SYSTEM_PROMPT |
| `26df3f7` | fix(orchestration) | Phase 5 runaway — Haiku swap + JSON-from-prose extract + session cleanup + Rule 8 |
| `0951c15` | feat(webui-agent) | Phase 3.4 — spatial label discovery in `_resolve_name` |

## Test count delta

Yesterday's wrap: 381 passing.

Today's checkpoints:
- 441150c: 381 → 389 (+8)
- 842a316: 389 → 394 (+5)
- 07025bb: 394 → 409 (+15)
- 2288b64: 409 → 413 (+4)
- 1e72aed: 413 → 420 (+7)
- 6329e96 + d8d1367: 420 → 427 (+7)
- 26df3f7: 427 → 437 (+10)
- 0951c15: 438 → 444 (+6)

Final: **444 passing**, 3 skipped (smoke-test hardware deps).

## Locked workflow rules (new today)

Two new feedback memories saved to `~/.claude/projects/.../memory/`:

1. **Model role split** (refined) — Opus explores/plans/risks/criteria; Sonnet implements step-by-step with tests interleaved; Haiku does lightweight delta checks only. Opus reviews ONLY for security-sensitive / complex / deviation cases. [feedback_model_role_split.md](../../C-/Users/filip/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md) (path is in user memory).

2. **Production LLM = Haiku 4.5 only** — every backend Claude API call (outer planner + inner plan drafter + any future LLM helper) MUST use `claude-haiku-4-5-20251001`. Never Sonnet/Opus in production paths. Fix the prompt before bumping the model. [feedback_production_llm.md](../../C-/Users/filip/.claude/projects/C--GIT-AI-configurator/memory/feedback_production_llm.md).

## Name resolution chain (after Phase 3.3 + 3.4)

`backend/webui_agent/semantic_dom._resolve_name(loc)`:

1. `aria-label`
2. `aria-labelledby` (resolves referenced element's text)
3. `inner_text` (the element's own visible text)
4. `<label for="id">` (HTML form labeling)
5. **Spatial discovery** (Phase 3.4) — closest text element above with horizontal overlap/alignment, via single `page.evaluate` JS call
6. `placeholder`
7. `title`
8. `name` attribute
9. `id` (skip Angular `ng-*` ids)
10. Return `""`

Phase 3.4 fixes Cisco's pattern where labels are separate `<a>`/`<div>` siblings without `<label for>` association. Spatial-derived names appear in describe_page output whenever the form is visible.

## Phase 5 architecture (after today)

Tools registered in [`tool_registry.py`](../backend/orchestration/tool_registry.py):
- `propose_webui_configure(intent: str, webui_path: str)` → outer Haiku-callable
- `webui_configure(action_id: str)` → outer Haiku-callable, requires APPROVED action_id

Internal-only helpers (no longer in `TOOL_SCHEMAS` since Phase 5 launch):
- `webui_open`, `webui_describe_page`, `webui_verify`, `webui_act`, `webui_act_by_intent`

Outer Haiku's only WebUI write path = propose_webui_configure → APPROVE → webui_configure. **Threat 1 (approval scope drift) is closed at the protocol level.**

Defense in depth:
- Layer 1: dispatcher `_REQUIRES_APPROVAL` check
- Layer 2: in-function `is_approved(action_id)` re-check
- Layer 3: QW3 sensitive-text deny-list in `_do_act_by_intent` (refuses Factory Reset / etc.)
- Layer 4: QW4 URL-origin guard in `_resolve_target_url` (rejects non-router hosts)
- Layer 5 (new today): nav map in outer SYSTEM_PROMPT scopes valid `webui_path` values
- Layer 6 (new today): inner Haiku SYSTEM_PROMPT verbatim-element rule + refuse-on-mismatch
- Layer 7 (new today): JSON-from-prose extraction recovers narration into structured plans
- Layer 8 (new today): Rule 8 in outer SYSTEM_PROMPT prevents rephrase-retry loops on errors

## Known gap — multi-step Phase 5 flows

Today's static-route test exposed: inner Haiku only sees ONE view at propose time. If the user's intent requires a page transition (e.g. click `Add` to open a form), inner Haiku can only plan up to the transition — not the steps after.

Result: single-step plan executes cleanly, form opens, but never gets filled. The `webui_configure` execute path doesn't re-describe between steps and doesn't re-invoke the inner planner.

**Recommended fix (general)**: multi-propose chain — server iterates after each step. See [next-session-kickoff.md](next-session-kickoff.md) for the detailed plan.

## Rollback points (all on origin)

| Tag | Commit | Restores to |
|---|---|---|
| `backup-20260515-1031` | `0951c15` | Current end-of-day state |
| `v0.3.6-security-review` | `601fd53` | Yesterday's end-of-day (pre-Phase 5) |
| `v0.3.5-catalog-shipped` | `db12595` | Pre-security-docs |
| `v0.3.4-ai-driver-ready` | `e7ef6db` | Phase 4 complete, pre-catalog |
| `v0.3.2-webui-flows-working` | `b084088` | Pre-AI-shift, hand-coded flows still pass |

If multi-propose chain goes sideways tomorrow: reset to `backup-20260515-1031`. The Phase 5 tools + catalog + nav map all keep working from this state.

## Tomorrow

See [next-session-kickoff.md](next-session-kickoff.md). Top item: multi-propose chain for general WebUI configuration. Second item: parallel CLI + AI config plan (`propose_cli_configure` / `cli_configure`).
