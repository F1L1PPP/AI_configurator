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

---

## Late evening additions (after 3b7a638)

### CI fix — drop frontend job (9d166ac)

`.github/workflows/ci.yml` had a leftover `frontend:` job from the Next.js era running `npm ci` against the deleted `frontend/package-lock.json`. Every push since the v0.5.0 swap was failing. Dropped the entire job. Backend tests still exercise the StaticFiles mount path implicitly — [tests/unit/test_routes_devices.py](../tests/unit/test_routes_devices.py) imports `backend.main`, which fails to load if `frontend/` doesn't exist. Left a NOTE block in the YAML explaining what was there and when to restore it (Phase 2 Next.js port).

### README + GitHub repo polish (uncommitted at end of day)

Top-level [README.md](../README.md) rewritten to match post-v0.5.0 state:

- Removed Node.js prerequisite and `cd frontend && npm install`
- Single `uvicorn backend.main:app --reload --port 8000` for both API and SPA
- New Screenshots section at the top — three placeholders pointing at `docs/screenshots/{dashboard,ai-configuration,devices}.png`
- Tightened install / run / test sections; pointer block to the key docs

`docs/screenshots/` created with `.gitkeep` so the directory lands in git. PNG files NOT yet on disk — Filip needs to save 3 screenshots from the chat into that directory before the commit lands, otherwise the README ships with broken image links.

GitHub repo description + topics drafted, ready to paste via the web UI (`gh` CLI is not authed locally):

- **Description**: `AI-powered Cisco router configuration with Claude. Chat → plan → human approves → Python executes. CLI + WebUI agents.`
- **Topics**: cisco, network-automation, ai-agent, claude, llm, fastapi, python, playwright, netmiko, rag, chromadb, human-in-the-loop, ios-xe

### Bug surfaced: CLI `set_interface_ip` silently failed

Filip ran `zmen ip na GigabitEthernet0/1/3 na ip 10.0.0.1 255.255.255.0` against the lab C1111-4P (action_id `act_20260518_ec1a69`). The chat showed success, Approve+Execute went through cleanly, but the IP wasn't actually on the interface afterward. Root cause stack:

1. **C1111-4P `Gi0/1/x` are hardware-locked L2 switchports.** [backend/cli_agent/write_tools.py:314](../backend/cli_agent/write_tools.py) already prepends `no switchport` to handle the C1111-4P L2 default. But on the C1111-4P the four `Gi0/1/0..Gi0/1/3` ports are part of the embedded EHWIC switch module — they are hardware-L2-only, and IOS XE rejects `no switchport` on them outright. After that rejection the subsequent `ip address` is rejected too (still a switchport), but `no shutdown` succeeds (always valid). The interface comes up with no IP. Workflow for getting an IP on traffic going to `Gi0/1/3` is the SVI pattern (create `interface vlan N` with the IP, then `switchport access vlan N` on the port).

2. **The write_tool reports success even when Netmiko's commands were rejected.** `conn.send_config_set([...])` returns the captured device output but doesn't raise on `% ` error markers unless an `error_pattern=` argument is passed. The code doesn't pass one, doesn't scan the captured output for `% `, and doesn't run a verify (`show running-config interface <name>`) afterward. The success log line `"GigabitEthernet0/1/3 → 10.0.0.1/255.255.255.0"` is a string interpolation of the *intent*, not a check of the post-write state.

3. **Empty commands block in the proposal UI.** The `IOS XE commands` code block in the chat showed empty — `propose_set_interface_ip` is a fast-path tool that returns just `{interface, ip, mask}`, no `commands` array. Frontend's `synthesizeProposal` falls back to `[]`. Operator has no preview of what's about to run before clicking Approve.

Fix queued as tomorrow's first chunk; see [docs/next-session-kickoff.md](next-session-kickoff.md).

### WebSocket origin allowlist gap

~30 rejections logged between 09:51 and 09:57 with `origin: http://127.0.0.1:8000`. Commit 60e1eb4 added `http://localhost:8000` to `allowed_origins` but not the IPv4 spelling. If the operator opens the app via `127.0.0.1:8000` instead of `localhost:8000`, WS handshake gets 403'd and the live event stream column never connects. After Filip switched to `localhost:8000` the WS started accepting at 10:20. Tomorrow: add `http://127.0.0.1:8000` to defaults.

### Sidebar + Dashboard still showing mock device

Filip's screenshots showed the left sidebar's `ACTIVE DEVICE` card and the Dashboard's `DEVICE OVERVIEW` panel both rendering Router-01 at 192.168.1.1 ISR 4321 — that's the mock data, not the real C1111-LAB at 192.168.10.1. The Devices table and the topbar `1 DEVICES` count both fetch correctly from `/api/devices` (commit 4 wired those). The sidebar (in `frontend/chrome.jsx` or `frontend/app.jsx`) and the Dashboard panel (in `frontend/screens-basic.jsx`) were never explicitly wired in the migration plan and still read directly from `window.MOCK_DEVICES`. Tomorrow's chunk 4.

### Late-evening commits on origin

- `9d166ac` — ci: drop frontend job (Next.js retired at v0.5.0)

Everything else above is uncommitted in the working tree until tomorrow's chunk 8.
