# What shipped today — 2026-05-14

Branch: `feature/bootstrap` · Start: `914419b` (this morning, after the
v0.3.2-webui-flows-working tag) · End: `db12595` (tagged
`v0.3.5-catalog-shipped`).

**Phase 4 is feature-complete.** The AI-driven WebUI driver works
end-to-end (semantic DOM + long-lived subprocess session + action
tools + intent resolver) and has been proven against the real Cisco
C1111 via a 12-route catalog walk. Phase 5 (the planner-facing
`propose_webui_configure`) is the only large piece remaining before
the v0.4.0-ai-first milestone.

## Commits (in order)

| Commit | Subject | Phase |
|---|---|---|
| `091d853` | docs(webui-agent): keep/archive matrix + subprocess isolation rationale | Phase 2 |
| `81b7d3a` | feat(webui-agent): semantic_dom.describe_page for AI-driven WebUI (Phase 3) | Phase 3 |
| `2b9f472` | feat(webui-agent): tighten describe_page schema (Phase 3.1) | Phase 3.1 |
| `d992e56` | feat(webui-agent): add view_id to describe_page output (Phase 3.2) | Phase 3.2 |
| `ea77495` | feat(webui-agent): WebUISession + webui_open vertical slice (Phase 4 slice 1) | Phase 4 slice 1 |
| `3571317` | feat(webui-agent): describe + verify ops + pre-snapshot in webui_open (Phase 4 slice 2 commit 1) | Phase 4 slice 2 |
| `74a371b` | feat(webui-agent): webui_act with self-heal + never-retry-click guard (Phase 4 slice 2 commit 2) | Phase 4 slice 2 |
| `e7ef6db` | feat(webui-agent): webui_act_by_intent intent resolver (Phase 4 slice 2 commit 3) | Phase 4 slice 2 |
| `feb89de` | test(webui-agent): manual smoke script for Phase 4 slice 2 | catalog prep |
| `75de685` | chore(scripts): catalog Cisco WebUI elements for Phase 5 planner grounding | catalog |
| `75d9676` | fix(scripts): add repo-root to sys.path so backend imports resolve | catalog fix 1 |
| `db12595` | fix(webui-agent): resolve relative paths to absolute URLs in session 'open' | catalog fix 2 |

13 commits total. Each passes ruff + mypy + pytest.

## Tags

| Tag | Commit | Meaning |
|---|---|---|
| `v0.3.3-ai-first-foundations` | `ea77495` | Phases 0–4 slice 1 (cost discipline, semantic_dom, WebUISession + webui_open) |
| `v0.3.4-ai-driver-ready` | `e7ef6db` | Phase 4 feature-complete (act + self-heal + intent resolver) |
| `v0.3.5-catalog-shipped` | `db12595` | **Today's end state** — catalog walked the real router, 296 elements captured |
| `backup-20260514-0935` | `b084088` | Morning safety net (pre-AI-shift) |
| `backup-20260514-1126` | `ea77495` | Mid-day backup (Phase 4 slice 1 boundary) |
| `backup-20260514-1242` | `db12595` | Wrap-of-day backup |

## Tests delta

| | Before | After | Delta |
|---|---|---|---|
| Total | 303 | 381 | **+78** |
| Skipped | 3 | 3 | 0 (smoke writes — `SMOKE_ALLOW_WRITES`) |

New tests landed alongside each commit:
- 17 in `tests/unit/test_semantic_dom.py` (Phase 3)
- 6 more in same file (Phase 3.1 — value/required + aria-labelledby + budget)
- 2 more in same file (Phase 3.2 — view_id)
- 7 in `tests/unit/test_webui_subprocess.py` (Phase 4 slice 1 — WebUISession protocol)
- 8 in `tests/unit/test_generic_driver.py` (Phase 4 slice 1 — webui_open)
- 8 more in same file (Phase 4 slice 2 commit 1 — describe/verify/pre-snap)
- 12 more in same file (Phase 4 slice 2 commit 2 — webui_act + soft-failure parametrisation)
- 3 more in same file (Phase 4 slice 2 commit 3 — webui_act_by_intent)
- 8 in `tests/unit/test_playwright_subprocess.py` (Phase 4 slice 2 commit 2 — child-loop **never-retry-click** + classification)
- 1 more in same file (Phase 4 slice 2 commit 3 — intent resolver)

The single most important test in the whole +78:
`tests/unit/test_playwright_subprocess.py::test_click_timeout_does_not_retry`
— asserts `mock_locator.click.call_count == 1` AND
`reply["failure_reason"] == "click_timeout_unsafe_retry"`. Catches the
CLAUDE.md §4 violation at the child-loop level if a future refactor
breaks the guard.

## New tools wired into the planner registry

Five new tools (in `_TOOL_FUNCS` and `TOOL_SCHEMAS`). Two are
HITL-gated writes (in `WRITE_TOOLS`):

| Tool | Read/Write | HITL |
|---|---|---|
| `webui_open(path, action_id, headless)` | read | no |
| `webui_describe_page(session_id)` | read | no |
| `webui_verify(session_id, text)` | read | no |
| **`webui_act(session_id, view_id, eid, action, action_id, value)`** | write | **yes** |
| **`webui_act_by_intent(session_id, intent, action_id)`** | write | **yes** |

All five schemas carry an explicit "DO NOT call standalone until
Phase 5 wires the planner" caveat so Haiku doesn't reach for them
ad-hoc.

## New scripts

| File | Purpose |
|---|---|
| `scripts/smoke_phase4_slice2.py` | Manual end-to-end smoke against the real C1111. Walks open → describe → act_by_intent (fill Host Name with `PHASE4-DEMO-DO-NOT-APPLY`) → verify. No Apply click. |
| `scripts/catalog_webui_elements.py` | Walks a configurable list of hash routes, calls describe_page on each, writes `artifacts/webui-catalog/catalog-<ts>.json` + `.md`. Run once today against `192.168.10.1`. |

## Real-router catalog walk (today's main deliverable)

12 hash routes attempted; **5 real distinct pages** identified, **7
routes silently redirect to `/dashboard`** (Cisco's WebUI handles
those sidebar items via click handlers, not URL routing).

| Route | Status | Title | Elements |
|---|---|---|---|
| `/webui/#/general` | ✅ Real | Administration - Device | 21 |
| `/webui/#/vlan` | ✅ Real | Configuration - VLAN | 30 |
| `/webui/#/troubleshooting` | ✅ Real | Troubleshooting | 17 |
| `/webui/#/dhcp` | ✅ Real | Administration - DHCP Pools | 30 |
| `/webui/#/dashboard` | ✅ Real | Dashboard | 26 |
| `/webui/#/monitoring`, `/configuration`, `/administration`, `/interfaces`, `/routing`, `/users`, `/dayZeroRouting` | ⚠ Redirected | Dashboard (all) | 26 / 16 each |

Total: 296 elements captured. JSON + markdown sitting in
`artifacts/webui-catalog/catalog-20260514-103441.{json,md}` (not
committed — `artifacts/` is gitignored).

**Two real bugs the catalog surfaced:**

1. **`<label for>` extraction gap**: hostname textbox on `/general`
   (`e_006`) has `value='LAB-R4'`, `required=yes`, but `name=''` —
   `_resolve_name` doesn't walk the `<label for="switchName">Host Name*</label>`
   association yet. Phase 3.3 ships this fix tomorrow.
2. **Icon-only links lack names**: 10 link elements on `/general`
   (`e_011`–`e_020`) have `name=''` — would need `title` attribute
   extraction or vision fallback.

## Security re-review (also today)

The 2026-05-13 pre-phase security review was re-evaluated against
this morning's-through-tonight's code. Verdict in
[docs/security-review-2026-05-14.md](security-review-2026-05-14.md):

- **1 of 9** hardening steps mitigated (evidence trail per click — by
  `_do_act` calling `ev.step()`).
- **6 of 9** still open. Four highest-impact: approval scope drift
  (Threat 1), PDF prompt injection (Threat 2), untrusted selectors +
  nav (Threat 3), docs contradiction (PROJECT_PLAN.md +
  CLAUDE_INSTRUCTIONS.md still describe deterministic Playwright).
- **2 of 9** intentionally deferred (rate limiting + Playwright
  tracing).

Top 3 quick wins (≤30 min total) for tomorrow morning:
1. Control-char sanitizer in `core/logging.py` (closes Threat 5).
2. `<doc_chunk>` delimiter + SYSTEM_PROMPT warning (partial Threat 2).
3. Sensitive-text deny-list in `_do_act_by_intent` + URL-origin guard
   in `_resolve_target_url` (partial Threat 3).

## Phase status

| Phase | Description | Status |
|---|---|---|
| 0 | Milestone + backup tags | ✅ (pre-session, kicked off this morning) |
| 1 | Cost discipline (caching + telemetry + RAG cap) | ✅ |
| 2 | Keep/archive matrix README | ✅ |
| 3 | semantic_dom.describe_page | ✅ |
| 3.1 | value/required + _MAX_NAME_LEN=50 | ✅ |
| 3.2 | view_id cookie | ✅ |
| 3.3 | `<label for>` + title + name + id in `_resolve_name` | 🔜 tomorrow |
| 4 slice 1 | WebUISession + webui_open vertical | ✅ |
| 4 slice 2 | webui_act + self-heal + intent resolver | ✅ |
| 4.1 | CANCELLING action state for mid-flow stop | 🔜 deferred |
| 5 | propose_webui_configure + planner system prompt + RAG | 🔜 tomorrow (after security quick wins) |
| 6 | Vision on-demand | not started |
| 7 | Archive sweep + `v0.4.0-ai-first` tag | not started |

## Operating notes (for tomorrow)

- HEAD: `db12595`. Working tree clean.
- Worktree: `C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5\`.
- Worktree venv at `.venv/Scripts/python.exe` — that's where Phase 4
  slice 2 code lives; main checkout venv at `C:\GIT\AI_configurator\.venv`
  doesn't have it.
- Catalog files at `artifacts/webui-catalog/catalog-20260514-103441.{json,md}`
  — reference them when planning Phase 5 RAG grounding.
- Pre-commit hook will reformat (`ruff format`) on commit. Standard
  re-stage + re-commit if it fires. Annoying but harmless.
