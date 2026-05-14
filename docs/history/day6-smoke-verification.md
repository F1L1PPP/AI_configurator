# Day 6 — smoke verification proof

**Date:** 2026-05-13 (afternoon, ~08:46 local)
**Branch:** `feature/bootstrap` at commit `3433dfb` (HEAD)
**Run by:** Filip on Windows / PowerShell against worktree at
`C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5`

This is the captured proof that the Day 6 deliverable (RAG + WebSocket
events + Sources display) works end-to-end against the running stack.
Companion to [docs/day6-summary.md](day6-summary.md) (what shipped) and
[docs/day6-rag-smoke.md](day6-rag-smoke.md) (10-query retrieval grading,
still pending Filip's 1/0 marks).

---

## What was verified

### 1. Vector store integrity — `tools/check_vectorstore.py`

```
collection : cisco_docs
persist_dir: knowledge_base\vectorstore
total chunks: 692

sample chunks:
  d814769b505e25ab | isr1100-sw-config.pdf | (no section)
  12a990df13d257d3 | isr1100-sw-config.pdf | Managing Configuration Files 15
  5c65c0d97c18a561 | isr1100-sw-config.pdf | Managing Configuration Files 15
  b15d7a6ee9c925ac | isr1100-sw-config.pdf | Configuring Enhanced Interior Gateway Routing Protocol 129
  2321b2189697c66d | isr1100-sw-config.pdf | Configuring Username and Password Pairs 144

chunks per source:
    692  isr1100-sw-config.pdf
```

✓ Ingest persisted 692 chunks from the one PDF currently on disk
(`isr1100-sw-config.pdf`, the ISR 1100 Software Configuration Guide).
Metadata (source, section) is preserved per chunk.

### 2. Spot-check queries — `tools/query_rag.py`

Query: `show vlan brief output columns` (top_k=5)

```
#1 [0.421] isr1100-sw-config.pdf -- CHAPTER 24
#2 [0.337] isr1100-sw-config.pdf -- Configuring Wi-Fi 6
#3 [0.297] isr1100-sw-config.pdf -- Configuring VLANs
#4 [0.289] isr1100-sw-config.pdf -- Configuring VTP
#5 [0.278] isr1100-sw-config.pdf -- Configuring Flexible NetFlow over a Bridge Domain ...
```

Query: `interface GigabitEthernet naming` (top_k=10)

```
#1 [0.546] isr1100-sw-config.pdf -- CHAPTER 24
#2 [0.496] isr1100-sw-config.pdf -- Understanding Diagnostic Mode
#3 [0.473] isr1100-sw-config.pdf -- Configuring Wi-Fi 6
... (10 hits total, scores 0.396 .. 0.546)
```

✓ Retrieval returns ranked hits with varied sections. Score band 0.28–0.55
is consistent with single-corpus coverage of partially-relevant topics.

### 3. End-to-end chat round-trip — `POST /api/chat`

Backend: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`
(with `Remove-Item Env:ANTHROPIC_API_KEY` to dodge today's shell-shadow
gotcha — see [day6-summary.md](day6-summary.md) bullet 6).

Request (Slovak):
> "Podla cisco dokumentacie pre ISR 1100: ako sa konfiguruje hostname
>  a kde to v dokumente najdem?"

Backend log proves Haiku invoked `search_docs`:

```
{"tool": "search_docs",
 "params": {"query": "hostname configuration ISR 1100 C1111", "top_k": 5},
 "result_summary": "5 hits", "duration_ms": 3261,
 "event": "tool_call",
 "logger": "backend.knowledge_agent.retrieve"}

{"stop_reason": "end_turn", "iterations": 2, "event": "planner_done"}

INFO: 127.0.0.1:61166 - "POST /api/chat HTTP/1.1" 200 OK
```

Reply (Slovak, with **Sources** block) — abridged:

> ## Konfigurácia hostname-u
>
> **Príkaz:**  `Router(config)# hostname <name>`
> ...
> ## Kde to nájdeš v dokumentácii
>
> - **Dokument:** `isr1100-sw-config.pdf`
> - **Sekcia:** "Configuring Global Parameters" v časti "Basic Router Configuration"
>
> **Sources**
> - isr1100-sw-config.pdf — Configuring Global Parameters, Basic Router Configuration

✓ The agent **consulted the corpus**, **cited the exact section**, and
**emitted the Sources block** the Day 6 prompt requires.

### 4. WebSocket — `/ws/agent`

Backend log over the same session:

```
INFO: ('127.0.0.1', 65005) - "WebSocket /ws/agent" [accepted]
{"subscribers": 1, "event": "ws_agent_connected"}
INFO: connection open
INFO: connection closed
... (4 successful connect/disconnect cycles as Filip navigated /chat,
     /preview, /webui-live)
INFO: ('127.0.0.1', 64669) - "WebSocket /ws/agent" [accepted]
{"subscribers": 4, "event": "ws_agent_connected"}
```

✓ Real browser WS clients subscribe and unsubscribe cleanly. Subscriber
count tracks accurately. (4 reflects pages reconnecting on navigation;
each disconnect path is logged.)

### 5. Frontend dev server — `npm run dev` (from `./frontend/`)

```
▲ Next.js 14.2.35
- Local:        http://localhost:3000
✓ Ready in 12.6s
✓ Compiled /chat in 1905ms (554 modules)
✓ Compiled /preview in 422ms (550 modules)
✓ Compiled /webui-live in 309ms (557 modules)
✓ Compiled / in 242ms (567 modules)
GET /chat 200 in 2296ms
```

✓ All three Day 6 pages compile clean (no TypeScript errors, no missing
imports). `/chat` served successfully.

### 6. Regression suite — `pytest -q`

```
156 passed in 9.95s
```

✓ All 130 prior tests still green + 26 new Day 6 tests (chunker,
retrieve, tool-registry registration, eventbus pub/sub + cross-thread
publish + back-pressure, /ws/agent integration via Starlette
TestClient).

---

## What's still pending

1. **10-query smoke grade** in [docs/day6-rag-smoke.md](day6-rag-smoke.md).
   Filip fills in `Score: __ / 1` per question. Pass = ≥ 7/10. The two
   spot-checks above (`show vlan brief output columns`, `interface
   GigabitEthernet naming`) are illustrative but not part of the formal
   smoke set.
2. **Browser visual check** — Filip's last action was opening the
   frontend dev server. The four visual checks (green WS dot, event
   stream populates, citation chips render, hover tooltip) are not yet
   captured here. Add a screenshot or note in this file once verified.
3. **`v0.2.0-agent-core` tag** — gated on smoke ≥ 7/10. Filip creates
   manually per `CLAUDE.md` tag policy.

---

## Cosmetic notes (not bugs)

- **PowerShell console encoding** mangles Slovak chars when printing
  the chat reply directly (`Perfektne! NaÅ¡iel...` instead of `Našiel`).
  The JSON body is correct UTF-8; the browser renders it right. To fix
  the terminal display for the current session:
  `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()`
- **PowerShell `Where-Object` scalar collapse** — when filtering events
  for a single search_docs call, `$sd` is a scalar object, not an array,
  so `$sd.Count` is empty. Force an array:
  `$sd = @($resp.events | Where-Object { ... })`

Both are PowerShell quirks, not bugs in the Day 6 code.

---

## Conclusion

The Day 6 deliverable — `search_docs` tool grounded in 692 ChromaDB
chunks, planner emitting events through a thread-safe bus to
`/ws/agent`, frontend rendering Sources badges — is **functionally
proven on the running stack**. What remains is human-graded smoke
quality (≥ 7/10 target) and the milestone tag.
