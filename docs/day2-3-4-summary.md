# Days 2 + 3 + 4 — three plan-days in one calendar day

**Date:** 2026-05-12
**Branch:** `feature/bootstrap` (still off `develop`)
**Plan-days collapsed:** Day 2 (CLI read) + Day 3 (CLI write + HITL + snapshots)
+ Day 4 (orchestrator + tool registry + chat API)
**Milestone tag created:** `v0.1.0-cli-core` (at the close of Day 3, before
Day 4 began — user override per CLAUDE.md hands-off rule)
**Test count:** 7 → 66 (+59 new tests, all green)
**Status:** Days 1–4 of the 10-day plan are complete. Banking time before
Day 5 (RAG).

---

## What shipped (in order)

### Phase 0 — Router pre-flight (closed before Day 2)

Real Cisco C1111 cabled, `.env` populated with `ROUTER_HOST=192.168.10.1`,
SSH + WebUI credentials, host key pre-accepted. `v0.0.1-bootstrap` already
exists from yesterday.

### Day 2 — CLI read layer (commit `0ffad60`)

- `backend/cli_agent/connection.py` — Netmiko connection pool, one persistent
  SSH session per (host, user), retries only on connect (never on a send),
  surfaces a helpful RuntimeError when the host key hasn't been accepted.
- `backend/cli_agent/parsers.py` — `ntc-templates==9.1.0` wrapper; falls back
  to raw string when no template exists for the command.
- `backend/cli_agent/read_tools.py` — `show_version`, `show_ip_interface_brief`,
  `show_running_config`, `show_vlan_brief`. Each logs one JSONL line to
  `logs/actions.log` with `tool/params/result_summary/duration_ms`.
- `backend/api/routes_logs.py` — `GET /api/logs/recent?limit=N` reads the last
  N JSONL lines, newest first.
- `frontend/components/dashboard/RecentActions.tsx` + `lib/api.ts` —
  client component polls every 3 s and replaces the mock data.
- 13 new tests (`test_cli_connection`, `test_cli_parsers`, `test_cli_read_tools`).

**Smoke verified on real router**: `show_ip_interface_brief()` returned 7
interfaces parsed as dicts; `Vlan1` at `192.168.10.1 up/up` confirmed the
management interface.

### Day 3 — CLI write + HITL + snapshots + Preview wired (commits `8580b7d`, `3220a96`)

- `backend/cli_agent/snapshots.py` — `take_snapshot(action_id, phase)` runs
  `show running-config` + `show version` + `show ip int brief` and saves all
  three to `artifacts/device-snapshots/<action_id>/<phase>/`.
- `backend/cli_agent/write_tools.py` — `set_hostname`, `set_interface_ip`. Both
  refuse without an approved `action_id`. Pre-snapshot → config push → post-
  snapshot → mark EXECUTED. Never auto-retry on failure.
- `backend/services/restore.py` — `restore_config(snapshot_path)` reads
  `running-config.txt`, strips IOS headers/comments, sends remaining lines via
  `send_config_set`. Rollback path only.
- `backend/orchestration/confirmations.py` — `ActionState` enum
  (PROPOSED/APPROVED/REJECTED/EXECUTED/VERIFIED/FAILED), `propose_action`,
  `approve_action`, `reject_action`, `is_approved`, `_reset_for_testing`.
  In-memory store (SQLite migration deferred to Day 12).
- `backend/api/routes_approvals.py` — `POST /api/approve/{id}`, `POST
  /api/reject/{id}`, `GET /api/actions/{id}`.
- `frontend/components/preview/ApprovalButtons.tsx` — real client component
  with `idle/loading/approved/rejected/error` states, POSTs to the approval
  endpoints.
- `frontend/app/preview/page.tsx` — reads `?action_id=` from `searchParams`,
  passes to the button component.
- 19 new tests (`test_orchestration`, `test_cli_write_tools`).

**Smoke verified on real router (full round-trip):**
1. propose → APPROVED → `set_hostname("LAB-R1")` → hostname changed in 2.1 s
2. snapshots written: 6 files (pre/post × running-config/version/ip-int-brief)
3. `restore_config(pre/)` ran 140 config lines, hostname back to `c1111-lab`

**Bug found and fixed during smoke** (commit `3220a96`):
Netmiko's cached `base_prompt` stays stuck on the old hostname after
`hostname X` is sent; the next `send_config_set` times out waiting for the
old prompt pattern. Three-part fix: `pool.invalidate(host, user)` added,
called from `set_hostname` after the config push; `conn.find_prompt()` added
before `send_config_set` in `restore_config`; on send_config_set failure,
attempt `exit_config_mode()` then invalidate if that also fails.

**Tag created:** `v0.1.0-cli-core` (annotated, pushed to origin).

### Day 4 — Orchestrator + tool registry + chat API (commits `e5c4414`, `684ead8`)

- `backend/orchestration/tool_registry.py` — 8 tools in Anthropic format:
  - 4 read (no approval): `show_version`, `show_ip_interface_brief`,
    `show_running_config`, `show_vlan_brief`
  - 2 propose (register action_id, don't touch router): `propose_set_hostname`,
    `propose_set_interface_ip`
  - 2 execute (require APPROVED action_id): `set_hostname`, `set_interface_ip`
  - Dispatcher wraps non-dict results, catches `NotApproved` / `TypeError` /
    any other exception so the planner never dies mid-loop.
- `backend/orchestration/planner.py` — Anthropic SDK tool-use loop. Bilingual
  SK/EN system prompt with hard rules (always propose-first for writes, never
  auto-retry, refuse out-of-scope: OSPF/ACL/DHCP). Max 8 iterations. Emits
  structured events: `agent_thinking`, `tool_call`, `tool_result`,
  `awaiting_approval`, `error`. Returns full message history for follow-up
  turns.
- `backend/api/routes_chat.py` — `POST /api/chat`. Surfaces pending
  `action_id` at the top level so the frontend can open `/preview?action_id=…`
  directly.
- `requirements.txt` — pinned `anthropic==0.101.0`.
- Initial choice was `claude-sonnet-4-6`; **swapped to `claude-haiku-4-5-20251001`**
  (commit `684ead8`) — 8 well-defined tools and short outputs don't need
  Sonnet's reasoning depth; Haiku 4.5 is ~2× faster, ~5× cheaper at the same
  accuracy on this workload.
- 19 new tests (`test_tool_registry`, `test_planner`). Anthropic client is
  mocked in unit tests — zero API spend per test run.

**Smoke verified on real router (full natural-language round-trip):**
1. `httpx.post(/api/chat, {"message": "ukáž mi rozhrania"})` → Haiku 4.5 picked
   `show_ip_interface_brief` → router returned 7 interfaces → response in
   Slovak with a Markdown table and summary in ~3 s.
2. `httpx.post(/api/chat, {"message": "zmeň hostname na LAB-R1"})` → Haiku 4.5
   picked `propose_set_hostname` → returned `action_id`,
   `awaiting_approval` populated at top level.
3. `httpx.post(/api/approve/{id})` → state APPROVED.
4. Follow-up `httpx.post(/api/chat, {"message": "…schválená, vykonaj ju",
   "history": ...})` → Haiku 4.5 picked `set_hostname` with the approved
   `action_id` → hostname changed to LAB-R1 in **1.29 s** including pre/post
   snapshots.

---

## What's verified working RIGHT NOW (2026-05-12)

| Check | Result |
|---|---|
| `python -m ruff check backend/ tests/` | All checks passed |
| `python -m pytest tests/unit/ -q` | 66 passed, 0 failed |
| `uvicorn backend.main:app --reload` | Starts clean, all 4 routers loaded |
| `GET /healthz` | 200 `{"status":"ok"}` |
| `GET /api/logs/recent?limit=5` | Returns last 5 JSONL entries newest-first |
| `POST /api/approve/{id}` | 200, state transitions to APPROVED |
| `POST /api/chat` (read scenario) | 200, parsed interfaces + Slovak summary |
| `POST /api/chat` (write scenario) | 200, proposes action_id, returns `awaiting_approval` |
| `POST /api/chat` after approval | 200, executes write, returns success message |
| Dashboard `/` Recent Activity | Live entries appear within 3 s of any tool call |
| Preview `/preview?action_id=…` | APPROVE button POSTs to the real backend |
| `artifacts/device-snapshots/<id>/{pre,post}/*.txt` | 6 files per action, all written |

---

## What's open / deferred

- **`set_interface_ip` real-router smoke** — built and unit-tested but never
  smoke-tested live. `Gi0/1/0` is the active management interface, so changing
  its IP would break the SSH session mid-test. Safe to test against
  `Gi0/0/0` (admin-down) any time.
- **Playwright `codegen` recording** — script 06 covers the same ground
  programmatically; the codegen capture is the version-specific selector
  ground truth and can be done any time before Day 7 WebUI work begins.
- **WebSocket streaming for chat** — Day 4 only built the synchronous
  `POST /api/chat`. The `agent_thinking`/`tool_call`/`tool_result` events
  fire but the frontend chat page (`/chat`) still shows mocked data. Wiring
  this up is a Day 11 polish task per the revised plan.

---

## What's next

### Day 5 — RAG minimum

`knowledge_agent/ingest.py` — curated Cisco docs (~10 MB) chunked,
embedded via `all-MiniLM-L6-v2`, persisted to ChromaDB. `retrieve.search_docs`
returns chunks with source + section. Orchestrator system prompt updated to
call `search_docs` before unfamiliar configs. Responses include a "Sources"
section. Smoke: 10 hand-graded relevance queries, target ≥ 7/10.

### Schedule check

| Plan day | Original calendar date | Actual calendar date |
|---|---|---|
| Day 1 | 2026-05-11 | 2026-05-11 ✓ |
| Day 2 | 2026-05-12 | 2026-05-12 ✓ |
| Day 3 | 2026-05-13 | 2026-05-12 (1 day early) |
| Day 4 | 2026-05-14 | 2026-05-12 (2 days early) |
| Day 5 | 2026-05-15 | TBD |

**Banked: 2 calendar days.** If Day 5 (RAG) lands tomorrow as planned, the
v0.4.0-alpha.1 freeze on Day 9 could shift up to Day 7 and leave Days 8–10
as pure GUI polish + technical report + demo recording.
