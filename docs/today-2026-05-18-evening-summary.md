# 2026-05-18 evening wrap — new frontend integrated, v0.5.0 cut

16 commits since the pre-redesign freeze. The branch crossed from "old Next.js prototype" to "new static-React design wired into the real backend end-to-end" and the milestone tag `v0.5.0-new-frontend` landed.

## Three phases

### Phase A — Pre-redesign cleanup (e6ebde2 → fcb72ea, 3 commits)

Audit-driven sweep before any redesign code landed. Ran four parallel agents (security review + dead-code audit + docs drift + test hygiene) against the alpha.4 baseline. Security review came back NO_FINDINGS. The audits surfaced:

- 4 high-confidence dead-code deletes — `backend/services/restore.py` (never imported), `scripts/smoke_phase4_slice2.py` (one-shot Phase 4 dev smoke), an unused fixture in `test_routes_execute_toctou.py`, and committed `.pyc` files. Plus one stale doc reference in `docs/smoke-scenarios.md` that pointed at the deleted function. Net 303 deletions, 3 insertions.
- 3 completed planning docs archived to `docs/history/` — `security-review-2026-05-14.md` (threats closed), `rag-sources.md` (corpus shipped), `codegen-howto.md` (Day-4 procedure). `docs/history/README.md` updated.
- 5 heavy-drift docs rewritten by 5 parallel agents to reflect the AI-first architecture: `PROJECT_PLAN.md` (§4.3 hybrid model + §5 repo layout + §6.2 tag chain + §7 milestone summary), `CLAUDE_INSTRUCTIONS.md` (scope-lock + production-Haiku stamp), `docs/how-it-works.md` (added §7.4 generic WebUI configure + §7.5 generic CLI configure), `docs/technical_report.md` (outline + post-alpha scenarios), `docs/plan-ai-first-webui.md` (Phases 0-5 marked ✅ DONE). 544 insertions, 337 deletions.

### Phase B — Frontend migration plan + execution (6d6744c → a285912, 12 commits)

Filip unzipped the new design into `frontend-new/`. It turned out to be flat .jsx files + CDN React + Babel-in-browser (no bundler, components on `window.*`) — incompatible shape with the existing Next.js setup. After scoping, picked the phased approach:

- Phase 1 (this session) — wire static design to real backend
- Phase 2 (future) — port to Next.js with TypeScript

Built and ran a 3-tier agent workflow per commit: Sonnet implements + writes tests inline → Haiku verifies file-scope + naming + lint → Opus commits, escalates on flagged divergence, reviews the highest-risk commit (WebSocket) directly.

**Backend changes** (1 commit, 60e1eb4):

- New `GET /api/devices` returning the real lab C1111 row (192.168.10.1, C1111-4P, IOS XE 17.6.3a — not the mock's fake 192.168.1.x)
- `StaticFiles(html=True)` mount at `/` so a single uvicorn process serves both API and SPA (zero CORS preflight, same-origin WS handshake)
- `localhost:8000` added to `allowed_origins` defaults
- 2 new test assertions in `test_routes_devices.py`

**Frontend integration** (9 commits, f62ae69 → a285912):

- New adapter file `frontend/api.jsx` exposing `window.api` with 8 methods (`fetchDevices`, `fetchRecentActivity`, `fetchPreview`, `sendChat`, `approveAction`, `rejectAction`, `executeAction`, `connectAgentWs`). The WebSocket client is a port of `frontend/lib/ws.ts`'s reconnect logic (500ms → 10s backoff, max 20 attempts, `closedByCaller` flag) — the designer's naive `new WebSocket(...)` snippet from the README was explicitly avoided.
- DashboardScreen → `/api/logs/recent` with first-paint mock fallback + shape adapter mapping log entries to `{id, text, time, kind}`
- DevicesScreen + topbar count → `/api/devices`
- ChatScreen `send()` → `/api/chat` with multi-turn history; `synthesizeProposal(reply)` builds the proposal bubble from `reply.events` with defensive fallback when no `propose_*` tool_call is found
- WebSocket subscription via `useEffect` with cleanup; `adapterEventToStreamLine(ev)` maps all 7 backend event types (`agent_thinking`, `tool_call`, `tool_result`, `awaiting_approval`, `applied`, `verified`, `error`) to the existing `{line, kind}` shape; deleted the synthetic `buildExecuteStream()` forEach path
- Approve/Reject/Execute buttons → real backend endpoints; async with `try/catch/finally`; phase state machine preserved (`idle → thinking → awaiting → executing → done`)
- PreviewScreen → `/api/actions/{id}` with three render branches (loading / has-diff / no-snapshot — the third is the honest answer in Phase 1 because the route doesn't return real pre/post running-config diffs yet)
- `mock-data.jsx` annotated as offline fallback, `INITIAL_CHAT` seed dropped

**Opus-caught bug during the commit-6 review**: commit 5 hijacked a pre-existing `history` state (semantic: completed-actions log) for chat-message multi-turn context. Two states now cleanly separated as `history` (action log) and `chatHistory` (planner context). Haiku had missed this; the deep review caught it.

### Phase C — Production swap + v0.5.0 tag (7e86def → b9ec7a5, 2 commits + 1 tag)

After manual browser smoke confirmed the new frontend works end-to-end:

- Deleted the old Next.js `frontend/` wholesale (33 tracked files). Preserved in git history + `frontend-design-backup/` safety net.
- `git mv frontend-new frontend` — rename detection intact, all moved files show R-status. `backend/main.py` `StaticFiles` mount path + `.gitignore` updated to match.
- About card in `screens-basic.jsx` stamped `Agent v0.5.0 · UI v0.5.0` (was "Agent v1.0 · UI prototype 0.4" from the designer's prototype label).
- Annotated tag `v0.5.0-new-frontend` cut at 7e86def and pushed.

## Current state

- HEAD: `b9ec7a5` (feature/bootstrap, pushed to origin)
- Tests: 523 passing (521 baseline + 2 new from `test_routes_devices.py`), 3 hardware-gated skips. Ruff clean.
- Tags on origin (newest first): `v0.5.0-new-frontend`, `v0.4.0-alpha.4-pre-redesign`, `v0.4.0-alpha.4-settle-wait`, `backup-20260518-090724`, plus the alpha.1-3 chain underneath.
- Manual smoke confirmed: `http://localhost:8000/` serves the new design same-origin; Dashboard reads real activity; Devices shows the real C1111 row; Chat round-trips through the real planner; WS pushes live events.

## What's left

Carry-overs from earlier in the day that didn't get worked on (still in `docs/next-session-kickoff.md`):

1. Anthropic 529 retry hardening (`max_retries=5` at three client sites + `OverloadedError` → `{"error": "llm_overloaded"}` wrapping) — would unblock hardware retests when Anthropic is having a moment
2. ISIS WebUI hardware retest (alpha.4's `_settle_page` still unverified end-to-end against the live modal race)
3. OSPF WebUI hardware retest (alpha.3's click-Add only tested on static route)
4. Router-id conflict pre-check (inner CLI planner refuses at propose time instead of post-execute)
5. Formal `v0.4.0-alpha.1` consolidation tag after 1-4 land

Also tomorrow-candidate: sweep the remaining "prototype" labels in `frontend/README.md`, `frontend/index.html` `<title>`, and `frontend/styles.css` header (cosmetic, ~1 commit).

`frontend-design-backup/` can stay for a few sessions; sweep when the new frontend feels stable.
