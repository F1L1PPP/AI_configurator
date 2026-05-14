# Day 6 — RAG + WebSocket events + Sources display

**Date:** 2026-05-13
**Branch:** `feature/bootstrap` (still off `develop`)
**Plan-day:** Day 6 (of the 10-day compressed plan)
**Milestone tag:** `v0.2.0-agent-core` — **not created** (tags are hands-off
per `CLAUDE.md`; Filip cuts the milestone tag manually after grading the
smoke).
**Test count:** 130 → 156 (+26 new tests, all green)
**Status:** Day 6 complete. Backend boots clean, /api/chat and /ws/agent
both verified against the real Anthropic Haiku 4.5 model.

---

## What shipped

### Phase A — RAG (commit `de4e22a` + fix `cee02b3`)

- `backend/knowledge_agent/chunking.py` — heading-aware sliding-window
  chunker. `Chunk` Pydantic model (id, source, section, text, tok_count);
  `chunk_text(text, source, chunk_tokens=250, chunk_overlap=30)`.
  Detects markdown `##`, `Chapter N`, numbered `5.1`, and the Cisco
  pattern `Configuring / About / Overview / Introduction / Understanding /
  Managing X`. Each chunk carries the last heading seen at or before its
  start offset.
- `backend/knowledge_agent/ingest.py` — one-shot CLI
  (`python -m backend.knowledge_agent.ingest`). For each PDF in
  `knowledge_base/docs/*.pdf`: extract text via `pypdf`, chunk, embed in
  batches of 64 via `sentence-transformers/all-MiniLM-L6-v2`, upsert into
  a `chromadb.PersistentClient` collection (`cisco_docs`, `hnsw:space=
  cosine`). Idempotent — chunk IDs are `sha1(source:offset)[:16]`, so
  re-running upserts in place. Bails non-zero with a clear pointer to
  `docs/rag-sources.md` if the docs folder is empty.
- `backend/knowledge_agent/retrieve.py` — lazy-loaded model + collection
  singletons; `search_docs(query, top_k=5)` embeds the query, queries
  Chroma, returns `{query, results: [{source, section, text, score}]}`.
  Score = `1 - cosine_distance`.
- `backend/orchestration/tool_registry.py` — `search_docs` registered
  as a read-only tool (no approval gate); the schema teaches Claude
  when to call it.
- `backend/orchestration/planner.py` — system prompt extended:
  "For ANY configuration question, call `search_docs` FIRST… then end
  your reply with a **Sources** section." Existing CLI/WebUI write
  rules unchanged.
- `backend/core/settings.py` — five new fields: `knowledge_base_dir`,
  `chroma_persist_dir`, `chroma_collection`, `embedding_model`,
  `rag_top_k`, `rag_chunk_tokens`, `rag_chunk_overlap`.
- `requirements.txt` — added `sentence-transformers==5.5.0`,
  `chromadb==1.5.9`, `pypdf==6.11.0`. Torch CPU build pinned via
  pytorch wheels index (documented at the top of the section).
- 12 new tests across `test_chunking`, `test_retrieve`,
  `test_tool_registry_search_docs` — all green.

### Phase B — Eventbus + WebSocket (commit `9606c0a`)

- `backend/core/eventbus.py` — thread-safe in-process pub/sub. Each
  subscriber gets a bounded `asyncio.Queue` (default 256). `publish()`
  iterates subscribers and schedules `put_nowait` on each via
  `loop.call_soon_threadsafe`, so the planner (which runs inside
  `run_in_threadpool`) can emit without knowing about the event loop.
  On `QueueFull`, drops the oldest event so publishers never block.
- `backend/api/routes_ws.py` — `GET /ws/agent` subscribes to the bus,
  forwards each event as JSON, cleanly unsubscribes on
  `WebSocketDisconnect`.
- `backend/orchestration/planner.py` — `_emit(events, kind, data)`
  helper appends to the in-memory trace AND publishes to the bus.
  Events: `agent_thinking`, `tool_call`, `tool_result`,
  `awaiting_approval`, `applied`, `verified`, `error`. The `applied`
  event fires when a write tool returns with `snapshot_post` and no
  `error` key.
- `backend/main.py` — `app.include_router(routes_ws.router)`.
- 7 new tests: bus pub/sub fundamentals, cross-thread publish,
  fan-out to multiple subscribers, unsubscribe-on-disconnect,
  back-pressure (drop-oldest), plus a Starlette TestClient integration
  test that hits `/ws/agent` for real.

### Phase C — Frontend (commits `c38e79e` + `7c8d947`)

- `frontend/lib/api.ts` — exports `WS_BASE` derived from `API_BASE` by
  swapping `http` → `ws`.
- `frontend/lib/ws.ts` — typed `AgentEvent` discriminated union
  mirroring the backend event types exactly, `connectAgentWs(onEvent,
  onStatus)` factory returning a `{close}` handle,
  `extractSources(events)` helper for the citation badges.
- `frontend/app/chat/page.tsx` — full rewrite. Removed
  `mockConversation`. State-driven `Msg[]` + `AgentEvent[]`. Input
  enabled, form submits to `endpoints.chat()`, conversation history
  threads back through `body.history`. Agent replies render citation
  badges (using the existing `border border-rule px-2 py-0.5
  text-[8px]` chip pattern from webui-live) when the turn called
  `search_docs`. Bottom-of-page live event panel shows the last 30 WS
  events; session header shows a colored dot for WS open/closed/error.
- `frontend/components/LiveEventStream.tsx` — shared component
  (subscribes to `/ws/agent`, renders a scrolling event timeline with
  a status dot). Used by /preview (below the approval buttons) and
  /webui-live (right column, alongside the still-mocked
  `ActionTimeline` + `PhaseProgress`).

---

## What's verified working

| Check | How | Result |
|---|---|---|
| Lint (ruff) | `ruff check backend/ tests/` | ✓ all checks passed |
| Backend tests | `pytest -q` | ✓ 156 passed in 12 s |
| Ingest | `python -m backend.knowledge_agent.ingest` | ✓ 692 chunks, 172,821 tokens, ~30 s |
| Backend boot | `uvicorn backend.main:app` | ✓ `/healthz` returns 200 |
| WS handshake | real `websockets` client → `ws://.../ws/agent` | ✓ connect + clean close |
| TS type-check | `npx tsc --noEmit` in `frontend/` | ✓ no errors |
| Chat round-trip | `POST /api/chat "What does the Cisco doc say…"` | ✓ 3 search_docs calls, sources in events |
| Slovak round-trip | `POST /api/chat "Ako sa zmeni hostname…"` | ✓ reply in Slovak, full event trace |

---

## What's open / deferred

1. **Smoke grading.** `docs/day6-rag-smoke.md` has the 10 queries + top-3
   retrieved chunks each. Filip fills in 1/0 inline; pass = ≥7/10. Until
   that's graded the v0.2.0-agent-core tag stays uncreated.
2. **Corpus is 1 of 7.** Only `isr1100-sw-config.pdf` is on disk
   (`knowledge_base/docs/`). Docs #2–#7 from `docs/rag-sources.md` (L2
   switching, WebUI user guide, the three command references, optional
   data sheet) will extend coverage on WebUI nav and switchport defaults.
   The pipeline is corpus-agnostic — just drop the PDFs in and re-run
   `ingest`; deterministic chunk IDs upsert without dupes.
3. **Tool-use is not always invoked.** Haiku 4.5 sometimes answers
   configuration questions from training data instead of calling
   `search_docs`, even with the prompt's "FIRST" rule. Two queries in
   the verification smoke triggered 0 search_docs calls (e.g. "Ako sa
   zmeni hostname"). When it IS invoked (e.g. "What does the Cisco doc
   say…"), citations flow through correctly. Possible Day 7 follow-ups:
   tighten prompt, or use Anthropic's `tool_choice={"type":"any"}` for
   read-only questions.
4. **Verified event not yet wired.** The planner emits `applied` after
   a successful write but never emits `verified` — verification is
   currently baked into the write tools (snapshot_post is the proxy).
   When `verify_*` becomes a separate planner step (Day 7+), the
   `verified` event slots in.
5. **WebUI screenshot stream.** `webui_agent` doesn't publish per-step
   screenshot events on the bus yet. The webui-live page falls back to
   the existing mocked phase + screenshot mock. Wiring this is a
   small, isolated follow-up.
6. **Environment gotcha (not Day 6 code).** Pydantic Settings was
   returning an empty `ANTHROPIC_API_KEY` despite the value being in
   `.env`. Root cause: an empty `ANTHROPIC_API_KEY=` in the parent
   shell (likely from a profile script) shadows the .env value, since
   process env wins over `env_file` in pydantic-settings precedence.
   Workaround for /api/chat: launch uvicorn with
   `unset ANTHROPIC_API_KEY; uvicorn …`, or remove the empty export
   from `.bashrc` / `.zshrc`.

---

## Day 7 plan (next)

Per `PROJECT_PLAN.md` §7 Day 7: add VLAN write tool (CLI + WebUI), extend
the curated corpus to docs #2–#7, re-run smoke to widen to switchport /
WebUI nav queries. Optional: tighten the `search_docs` invocation rate
via prompt or `tool_choice`.

3 calendar days remain banked against the 10-day schedule.
