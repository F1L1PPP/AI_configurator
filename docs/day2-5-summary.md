# Days 2 + 3 + 4 + 5 — four plan-days in one calendar day

**Date:** 2026-05-12
**Branch:** `feature/bootstrap` (still off `develop`)
**Plan-days collapsed:** Day 2 (CLI read) + Day 3 (CLI write + HITL +
snapshots) + Day 4 (orchestrator + tool registry + chat API + WebUI
scaffolds) + Day 5 (WebUI hostname flow + verify)
**Milestone tag created:** `v0.1.0-cli-core` (at the close of Day 3)
**Daily backup tags:** `backup-20260512-080555`, `-083242`, `-105202`,
`-123224` (end of day)
**Test count:** 7 → 122 (+115 new tests, all green)
**Status:** Days 1–5 of the 10-day plan are complete.
**WebUI hostname change proven end-to-end on real Cisco C1111 at 12:26.**

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

### Day 5 — WebUI hostname flow + verify (commit `a23dad3`, debug iters `bef6c13`..`b03c5ce`)

- `backend/webui_agent/pages/hostname_page.py` — Page Object Model.
  Direct hash-route nav to `/webui/#/general` bypasses the sidebar
  (which renders flakily under Playwright). `get_current_hostname`,
  `set_hostname` (focus + fill — `Locator.triple_click()` isn't on the
  Locator class in Playwright 1.49.1 sync), `apply`. On failure dumps
  full `input_inventory` (every `<input>` with name/id/ng-model/visible)
  for self-documenting diagnostics.
- `backend/webui_agent/flows/change_hostname.py` — composes browser +
  login + POM + verify + snapshots. Hard rules: HITL gate, pre-snapshot
  before any UI is touched, screenshots every step, SSH pool invalidated
  after success (same prompt-staleness bug as Day 3), CLI verify is the
  ground truth, no auto-retry on error.
- `backend/webui_agent/verify.py` — `verify_hostname` regex-anchored
  whole-line match against `show running-config`, `verify_vlan_exists`
  reserved for Day 7.
- `backend/orchestration/tool_registry.py` — `propose_webui_set_hostname`
  -> `webui_set_hostname` schemas + dispatch + `_REQUIRES_APPROVAL` gate.
- `backend/orchestration/planner.py` — system prompt teaches Claude when
  to pick CLI vs WebUI ("v prehliadači" / "demo" / "ukáž mi ako" → WebUI;
  otherwise CLI). Added rule for action_id reference: when user says
  "execute act_X", find the matching propose in history and call the
  execute tool from its `execute_tool` hint — never swap CLI↔WebUI mid-flow.

**Selector ground truth discovered today** (captured in `selectors/iosxe_default.yaml`):

- Cisco IOS XE 17.6.3a WebUI is **AngularJS 1.x + Kendo UI**, not Angular 2+.
- Hostname form is at Administration → Device → General (NOT "Device
  Properties" as plans assumed).
- Direct hash route: `https://192.168.10.1/webui/#/general` — bypasses
  the sidebar entirely.
- Form input is `<input name="switchName" id="switchName"
  data-ng-model="jsonData.general.name">` — the form is shared between
  switches and routers, hence "switchName".
- Apply button is `<button kendo-button="saveBtn"
  ng-click="apply('General')">Apply</button>` — initially disabled;
  `ng-change` on the input enables it after fill; Playwright `click()`
  waits for actionable state automatically.

**Smoke verified on real Cisco C1111 at 12:26 UTC+2 (action_id `act_20260512_441f6c`):**

1. propose + approve via Python REPL
2. SSH pre-snapshot saved (hostname `LAB-R3`)
3. Chromium launched headed
4. WebUI login completed (~2 s)
5. Direct nav to `#/general` — form rendered in ~13 s
6. Read `current=LAB-R3`, fill `LAB-R4`, screenshot
7. Apply clicked
8. SSH pool invalidated (hostname change → stale prompt)
9. `show running-config` → `verify_hostname expected=LAB-R4 found=True`
10. Post-snapshot saved

**Total: 23 seconds end-to-end.** 5 screenshots on disk at
`artifacts/screenshots/change_hostname_act_20260512_441f6c/`, 6 snapshot
files at `artifacts/device-snapshots/act_20260512_441f6c/{pre,post}/`.

**Deferred to Day 6** (originally Day 5 scope; bundles naturally with RAG):
- `backend/core/eventbus.py` + `GET /ws/agent` WebSocket route
- Refactor planner to publish events through the bus
- Frontend `lib/ws.ts` + `/chat` + `/webui-live` consume real events
- All ships with the RAG pieces under tag `v0.2.0-agent-core`.

---

## What's verified working RIGHT NOW (end-of-day 2026-05-12)

| Check | Result |
|---|---|
| `python -m ruff check backend/ tests/` | All checks passed |
| `python -m pytest tests/unit/ -q` | **122 passed**, 0 failed |
| `uvicorn backend.main:app --reload` | Starts clean, all 4 routers loaded |
| `GET /healthz` | 200 `{"status":"ok"}` |
| `GET /api/logs/recent?limit=5` | Returns last 5 JSONL entries newest-first |
| `POST /api/approve/{id}` | 200, state transitions to APPROVED |
| `POST /api/chat` (read scenario, CLI) | 200, parsed interfaces + Slovak summary |
| `POST /api/chat` (write scenario, CLI) | 200, proposes action_id, returns `awaiting_approval` |
| `POST /api/chat` after CLI approval | 200, executes write in 1.29 s, returns success |
| **WebUI hostname change end-to-end** | **23 s LAB-R3 → LAB-R4 against real C1111** ✓ |
| Dashboard `/` Recent Activity | Live entries appear within 3 s of any tool call |
| Preview `/preview?action_id=…` | APPROVE button POSTs to the real backend |
| `artifacts/device-snapshots/<id>/{pre,post}/*.txt` | 6 files per action, all written |
| `artifacts/screenshots/change_hostname_<id>/*.png` | 5 screenshots per WebUI run |

---

## What's open / deferred

- **`set_interface_ip` real-router smoke** — built and unit-tested but
  never smoke-tested live. `Gi0/1/0` is the active management interface,
  so changing its IP would break the SSH session mid-test. Safe to test
  against `Gi0/0/0` (admin-down) any time.
- **Playwright `codegen` recording** — script 06 + the diagnostic logs
  added in Day 5's debug session captured all the selectors we need.
  Codegen capture is the more verbose path; effectively done another way.
- **WebSocket streaming** — bundled into Day 6 alongside RAG (was
  originally Day 5 scope).

---

## What's next

### Day 6 — RAG + WebSocket + Sources display

- `backend/knowledge_agent/{ingest.py, chunking.py, retrieve.py}` —
  curated Cisco docs (~10 MB) chunked, embedded via
  `sentence-transformers/all-MiniLM-L6-v2`, persisted to ChromaDB.
- `search_docs(query, top_k=5)` returns chunks with source + section.
- Register `search_docs` as new orchestrator tool; system prompt asks
  Claude to consult docs before unfamiliar configs; responses include
  "Sources" section with citations.
- `backend/core/eventbus.py` async pub/sub + `GET /ws/agent` WebSocket
  route; planner publishes `agent_thinking`/`tool_call`/`tool_result`/
  `awaiting_approval`/`applied`/`verified`/`error` events.
- Frontend `lib/ws.ts` + `/chat` consumes message events + `/preview`
  + `/webui-live` replace mocked timelines with real WS events.
- Smoke: 10 hand-graded relevance queries, target ≥ 7/10.
- Tag: `v0.2.0-agent-core`.

### Schedule check

| Plan day | Original calendar date | Actual calendar date |
|---|---|---|
| Day 1 | 2026-05-11 | 2026-05-11 ✓ |
| Day 2 | 2026-05-12 | 2026-05-12 ✓ |
| Day 3 | 2026-05-13 | 2026-05-12 (1 day early) |
| Day 4 | 2026-05-14 | 2026-05-12 (2 days early) |
| Day 5 | 2026-05-15 | 2026-05-12 (3 days early) ✓ |
| Day 6 | 2026-05-16 | 2026-05-13 (target) |

**Banked: 3 calendar days.** If Day 6 (RAG + WebSocket) lands tomorrow,
the v0.4.0-alpha.1 freeze on Day 9 could shift up to Day 7 and leave
Days 8–10 as pure GUI polish + technical report + demo recording.
