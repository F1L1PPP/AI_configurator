# 2026-05-13 — One calendar day · Day 6 close-out + Day 7 build + 3 UX rounds

**Branch:** `feature/bootstrap`
**Test count:** 130 → **202** (+72 passing, all green at every commit)
**Milestone tag created today:** `v0.2.0-agent-core` (Day 6)
**Pending tag (awaits Filip's real-router proof):** `v0.3.0-webui-core` (Day 7)
**Backup tags created today (4):** `backup-20260513-0701`, `0900`, `0920`, plus this evening's

What started as "start Day 6" turned into shipping the entire Day-6
deliverable, fixing 6 Copilot review findings, building all of Day 7
(WebUI VLAN + smoke harness + Quick Actions), and three rounds of UX
fixes after real-router testing turned up usability gaps. 21 commits in
one calendar day.

---

## Day 6 (RAG + WebSocket + Sources) — closed

**Plan-day target:** `v0.2.0-agent-core`.

### Phase A — RAG corpus + retrieval

- `backend/knowledge_agent/chunking.py` — heading-aware sliding-window chunker (250-tok windows, 30-tok overlap, sized for MiniLM-L6's 256-tok input limit). Pydantic `Chunk` model with stable sha1(source:offset) IDs.
- `backend/knowledge_agent/ingest.py` — one-shot CLI (`python -m backend.knowledge_agent.ingest`). Globs `knowledge_base/docs/*.pdf`, extracts with `pypdf`, embeds with `sentence-transformers/all-MiniLM-L6-v2`, persists to ChromaDB with cosine distance metric. Idempotent upsert.
- `backend/knowledge_agent/retrieve.py` — lazy-loaded singletons; `search_docs(query, top_k=5)` returns `{query, results: [{source, section, text, score}]}`.
- Registered `search_docs` as a read-only tool in `tool_registry.py` via a lightweight lazy wrapper so loading `tool_registry` doesn't drag in `torch` for non-RAG processes.
- Planner system prompt updated: agent calls `search_docs` before unfamiliar configs and appends a Sources section.
- 23 unit tests across chunking / retrieve / tool registration.

**Corpus:** 2 of 7 PDFs ingested → **772 chunks**, 192,654 tokens.
- `isr1100-sw-config.pdf` (594 pages → 692 chunks)
- `b-cisco-1100-series-hig.pdf` (118 pages → 80 chunks)

**Smoke:** 10-query hand-graded retrieval test. **7/10 PASS** (target ≥7/10). Three misses are corpus gaps, not retrieval failures — they need WebUI User Guide + Basic System Management Command Reference, neither yet ingested. Full breakdown in `docs/day6-rag-smoke.md`.

### Phase B — Eventbus + WebSocket

- `backend/core/eventbus.py` — `EventBus` with bounded `asyncio.Queue` per subscriber, thread-safe `publish()` via `loop.call_soon_threadsafe`. The planner runs inside `run_in_threadpool` and emits without knowing about the asyncio event loop. Drop-oldest back-pressure.
- `backend/api/routes_ws.py` — `GET /ws/agent` subscribes to the bus and forwards events as JSON.
- `backend/orchestration/planner.py` — `_emit(events, kind, data)` helper appends to the in-memory trace AND publishes to the bus. Events: `agent_thinking / tool_call / tool_result / awaiting_approval / applied / error`.
- 7 new tests (cross-thread publish, fan-out, back-pressure, WS integration via Starlette TestClient).

### Phase C — Frontend

- `frontend/lib/ws.ts` — typed `AgentEvent` discriminated union mirroring backend events; `connectAgentWs(onEvent, onStatus)`; `extractSources(events)` helper for citation badges.
- `frontend/app/chat/page.tsx` — full rewrite. Removed mocked conversation, wired POST to `/api/chat`, subscribed to `/ws/agent`, citation badges on agent replies that called `search_docs`.
- `frontend/components/LiveEventStream.tsx` — shared event-stream panel used by `/preview` and `/webui-live`.

### Code review pass (6 Copilot findings)

After PR review surfaced 6 issues, all fixed in commit `cf033e6`:

| # | Finding | Fix |
|---|---|---|
| HIGH | `/ws/agent` didn't detect idle disconnects → dead subscribers piled up | Race `q.get()` with `ws.receive_text()`; disconnect surfaces immediately |
| MEDIUM | `events` state grew without bound in chat | Cap at 200 via `prev.slice(-199)` |
| LOW | `_WRITE_TOOLS` duplicated | DRY into one canonical `WRITE_TOOLS` frozenset |
| LOW | `assert` in `retrieve.search_docs` strippable under `python -O` | Replaced with explicit `RagNotInitialized` check |
| LOW | No WS reconnect → backend restart silently froze UI | Exponential backoff 500ms → 10s |
| NIT | Silent JSON parse failures in WS | `console.warn` |

Plus a major perf win: moved heavy RAG imports (chromadb / sentence_transformers / torch) inside `_ensure_loaded()`. Test suite runtime **12.3s → 2.4s** (5×) because most tests don't touch RAG.

**Tag cut:** `v0.2.0-agent-core` (per Filip's explicit authorization).

---

## Day 7 (WebUI VLAN + smoke harness + Quick Actions) — built

Targeted `v0.3.0-webui-core`.

### Slab A — WebUI VLAN flow

- `backend/webui_agent/pages/vlan_page.py` — `VlanPage` POM. Day-5 lesson: don't walk the sidebar (Cisco IOS XE 17.6.3a renders it unreliably under Playwright). Navigate directly to `/webui/#/vlan`. After landing on SVI tab (default), click the VLAN tab. Methods: `goto / click_add / set_vlan_id / set_vlan_name / save / _dump_diagnostics(lbl)`.
- `backend/webui_agent/flows/add_access_vlan.py` — `add_access_vlan_via_webui(vlan_id, vlan_name, action_id, headless=False) -> dict`. Mirrors `change_hostname.py` shape: `_guard → pre-snapshot → browser → login → goto → click_add → set_id → set_name → save → pool.invalidate → verify_vlan_exists → post-snapshot → mark_executed`.
- `tool_registry.py` — two new schemas (`propose_webui_add_access_vlan` + `webui_add_access_vlan`); `webui_add_access_vlan` added to `WRITE_TOOLS`; planner prompt advertises VLAN tools.
- 26 unit tests (POM behaviour, flow happy/fail paths, registry dispatch).

### Slab B — Verify path

No new code needed. `verify_vlan_exists(vlan_id, name)` already existed in `verify.py:38-69` with 5 tests covering it. Flow uses it as-is.

### Slab C — Smoke harness

- `tests/smoke/__init__.py` + `tests/smoke/scenarios/` — 6 scenario files mirroring §2.
- `tests/smoke/conftest.py` — three skip fixtures: `router_reachable` / `writes_allowed` (gates on `SMOKE_ALLOW_WRITES=1`) / `webui_enabled`.
- `scripts/run_smoke_tests.py` — wraps `pytest tests/smoke/ -v` with an ASCII summary table.
- Default-run output (writes off): 3 pass / 0 fail / 3 skip. Day-8-ready for the 5× alpha-freeze loop.

### Slab D — Frontend Quick Actions

- `frontend/components/actions/ScenarioCard.tsx` — reusable card with `shipped | planned` status.
- `frontend/components/actions/ScenarioForm.tsx` — shared form shell: builds NL prompt → POSTs `/api/chat` → on `awaiting_approval`, redirects to `/preview?action_id=...` (was the design; superseded later by inline buttons — see UX round 3 below).
- `frontend/app/page.tsx` — Dashboard Quick Actions panel.
- `frontend/app/actions/page.tsx` — index page (6-card grid).
- `frontend/app/actions/change-hostname/` + `/add-vlan/` + `/set-interface-ip/` — three form pages.

---

## UX round 1 — fixes after first real-router test

After backend restart + browser refresh, Filip ran the VLAN flow. Three real bugs surfaced (commit `c6ac909`):

| Bug | Symptom | Fix |
|---|---|---|
| A | Approve doesn't auto-execute | New `POST /api/execute/{action_id}` endpoint + **EXECUTE NOW** button on `/preview` |
| B | Conversation history lost on page nav | Sidestepped — execute now bypasses chat round-trip |
| C | `/preview` without action_id shows confusing disabled button | Empty-state copy now points at `/chat` / `/actions` |

5 integration tests for `/api/execute` (404/403/200/500 paths).

Two more bugs from the SAME run (commit `fcc61f4`):

| Bug | Cause | Fix |
|---|---|---|
| Hostname execute crashed: `set_hostname() got an unexpected keyword argument 'name'` | `_propose_set_hostname` stored params as `{"name": ...}`, function expects `new_name=` | Changed propose helpers to use `{"new_name": ...}` |
| VLAN flow: "Configuration menu not visible" | `VlanPage.goto()` walked the sidebar (same Day-5 problem hostname had) | Rewrite to use direct hash route `/webui/#/vlan` + click VLAN tab |

---

## UX round 2 — second real-router test

Hostname CLI now worked. VLAN WebUI flow ran end-to-end through Playwright, "Configuration Successfully Applied" toast confirmed save — but verify failed with `name 'office' mismatch`. Screenshot of `06-06-saved.png` showed VLAN 40 in the table but **Name column empty**. Root cause: my `vlan_name` selector chain led with `{label: "Name"}` and Cisco's Name field is loose text (same gotcha as hostname's `switchName` input on Day 5). Silent skip in `set_vlan_name` meant VLAN was created with no name.

Commit `c6ac909` continued the UX work plus fixed this:

| Ask | Fix |
|---|---|
| Inline APPROVE/EXECUTE in chat (no `/preview` round-trip) | Stash `awaiting_approval` on the agent Msg; render `<ApprovalButtons />` directly under the bubble |
| Hide non-functional sidebar links | Sidebar trimmed to **Dashboard · AI Chat · Quick Actions · Preview · WebUI Live** |
| Hostname form: CLI vs WebUI choice | Two-button radio at top of `/actions/change-hostname` |
| VLAN Name field silent miss | New selector chain leads with `data-ng-model` / `id` / `placeholder` CSS; falls back to label; last resort positional ("2nd text input in modal") |
| `set_vlan_name` silent skip | Now dumps `vlan_input_inventory` when field missing |

---

## UX round 3 — third real-router test

Chat + VLAN + hostname now all worked. Agent still parroting "open Preview screen and approve" because `next_step` strings in the propose helpers and the planner's system prompt rule 1 still mentioned `/preview`. Plus three more asks (commits `b51025d`, final fix today):

| Ask | Fix |
|---|---|
| Agent stops mentioning Preview | All 5 propose helpers' `next_step` rewritten to point at inline APPROVE / EXECUTE NOW buttons; planner rule 1 explicitly says "STOP after proposing"; regression test enforces no `/preview` in any `next_step` |
| CLI VLAN tool (parity with WebUI) | `set_access_vlan(vlan_id, vlan_name, action_id)` — validators (1..4094 range, name regex, bool guard), pre/post snapshot, `vlan <id>` + ` name <name>` over SSH |
| VLAN form: CLI · WebUI choice | Same radio as hostname form |
| Quick Actions bigger | Full-width, 2-col grid at top of Dashboard (was a narrow right column) |
| Drop fake `Devices=1 / Sessions=3 / Actions=12` StatCards | Removed; new `<ActionsCount />` shows real "tool calls today" from `/api/logs/recent` (polls every 5s) |
| Interface IP: `% Invalid input detected` on `Gi0/1/2` | `Gi0/1/x` are L2 switchports on C1111; IOS rejects `ip address` on them. Now prepend `no switchport` to the config block — auto-converts to L3 if switchport, no-op if already routed |
| Weirdly placed globe logo on Dashboard | Removed MeshSphere from the Dashboard (was overlapping the new bottom-right panel) |

---

## Final state at end of day

```
git log --oneline --since="2026-05-13 00:00" 
22 commits

backup tags on origin (most recent):
  backup-20260513-2100 ← this evening (about to push)
  backup-20260513-0920
  backup-20260513-0900
  backup-20260513-0701

milestone tags on origin:
  v0.2.0-agent-core   ← Day 6 done
  v0.1.0-cli-core     ← Day 3
  v0.0.1-bootstrap    ← Day 1
```

**202 tests passing** (130 baseline → +72 today). Ruff clean. Frontend tsc clean.

### What works end-to-end via the chat right now

1. **CLI hostname change** — propose → inline APPROVE → EXECUTE NOW → `hostname X` runs in ~1 s.
2. **WebUI hostname change** — propose → APPROVE → EXECUTE NOW → headed Chromium → screenshots → CLI verify.
3. **CLI interface IP** — propose → APPROVE → EXECUTE NOW → `no switchport` + `ip address X Y` + `no shutdown` runs.
4. **CLI VLAN add** — propose → APPROVE → EXECUTE NOW → `vlan N / name X` runs.
5. **WebUI VLAN add** — propose → APPROVE → EXECUTE NOW → Chromium → CLI verify (selector for Name field tuned but pending another real-router test to confirm).
6. **RAG chat** — search_docs called automatically when prompt is doc-flavoured; Sources badges render on agent replies.

### What needs Day 8

- **5× clean smoke loop** against the cabled C1111 → `v0.4.0-alpha.1` tag + `release/alpha-1-freeze` branch.
- **Logs / Backups / Devices** pages (the three sidebar items removed today come back as real pages).
- **`v0.3.0-webui-core` tag** — awaits Filip's cabled-session proof of the VLAN WebUI flow with the new Name-field selector.
- **WebUI help guide ingest** (optional side-track) — would push smoke 7/10 → 9/10 by covering Q2/Q5/Q7.
