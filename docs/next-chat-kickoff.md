# Kickoff prompt for the next Claude chat

Copy everything between the `---` lines below into a fresh chat. It's
self-contained — the new chat won't see this conversation but will have
everything it needs to continue work.

---

I'm continuing work on the Cisco AI Config Agent project. Days 1–7 of
the 10-day compressed plan are DONE; today (start of Day 8) I'm
beginning the alpha freeze.

CRITICAL — we're on the 10-day compressed plan, NOT the 14-day plan.
The authoritative day-by-day is in `PROJECT_PLAN.md §7`. If you find
"14 day" references in docs, those are stale — the compressed plan is
what we follow.

WORKTREE LOCATION:
  C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5
  Branch: feature/bootstrap (off develop, never commit to main)

TAGS ON ORIGIN:
  v0.0.1-bootstrap            (Day 1)
  v0.1.0-cli-core             (Day 3)
  v0.2.0-agent-core           (Day 6 — RAG + WebSocket + Sources)
  v0.3.0-webui-core           ← Day 7, pending cabled-session proof
  backup-20260513-*           (4 backup tags from today)

WHAT'S DONE (proven on real Cisco C1111 at 192.168.10.1, IOS XE
17.6.3a) — 202 unit tests passing, ruff clean, frontend tsc clean:

- Day 1: bootstrap, FastAPI, Pydantic Settings, structlog, Next.js
  with Dashboard/Chat/Preview/WebUILive pages.
- Day 2: Netmiko pool, 4 read tools, ntc-templates parsing,
  `/api/logs/recent`, Dashboard polls every 3 s.
- Day 3: HITL approval gate (two-layer defense), `set_hostname` and
  `set_interface_ip` CLI write tools, pre/post snapshots, restore
  proven.
- Day 4: Anthropic SDK tool-use loop with `claude-haiku-4-5-20251001`,
  10 tools, `POST /api/chat` with `run_in_threadpool`, Slovak round-
  trip in 1.29 s.
- Day 5: WebUI hostname change via Playwright (23 s end-to-end, real
  router). Lesson learned: Cisco IOS XE 17.x sidebar renders
  unreliably under Playwright → POMs navigate via direct hash routes
  (`/webui/#/general` for hostname, `/webui/#/vlan` for VLAN).
- Day 6: RAG (sentence-transformers/all-MiniLM-L6-v2 → ChromaDB with
  cosine distance, 772 chunks from 2 of 7 curated PDFs in
  `knowledge_base/docs/`). `/ws/agent` WebSocket route streaming
  planner events (`agent_thinking / tool_call / tool_result /
  awaiting_approval / applied / error`). Frontend `lib/ws.ts` typed
  client, citation badges on `/chat`, LiveEventStream component on
  `/preview` and `/webui-live`. Smoke graded 7/10 PASS. Three misses
  are corpus gaps (need WebUI User Guide + Basic Sys Mgmt Cmd Ref).
- Day 7: WebUI VLAN add flow (`VlanPage` POM + `add_access_vlan_via_webui`
  flow, mirrors hostname). CLI VLAN tool (`set_access_vlan`) for
  parity. Smoke harness at `tests/smoke/` + `scripts/run_smoke_tests.py`
  — 3 read scenarios pass unconditionally, 3 write scenarios skip
  unless `SMOKE_ALLOW_WRITES=1`. Frontend Quick Actions launcher:
  `/actions/change-hostname / add-vlan / set-interface-ip` each with
  CLI vs WebUI radio choice (where applicable). Inline APPROVE /
  EXECUTE NOW buttons in chat — `POST /api/execute/{action_id}`
  dispatches the approved tool directly with no LLM round-trip. The
  agent's `next_step` text and system prompt rule 1 explicitly point
  to the inline buttons; regression test enforces no `/preview` in
  any propose helper's `next_step`.

KEY GOTCHAS LEARNED THE HARD WAY:

1. The Cisco IOS XE WebUI is AngularJS 1.x + Kendo UI, not Angular 2+.
2. Cisco WebUI sidebar renders flakily under Playwright — bypass with
   direct hash routes (POMs do this).
3. Cisco WebUI forms use loose text labels (not `<label>` elements).
   `get_by_label()` silently fails. Lead selector chains with
   `data-ng-model` / `id` / `placeholder` CSS; fall back to label;
   last resort positional.
4. The hostname input is `name="switchName"` (form is shared with
   switches). The save button is "Apply to Device" not "Apply" /
   "Save" / "OK" — list it FIRST in the selectors chain.
5. After hostname change, the pooled SSH `base_prompt` is stale —
   `pool.invalidate(host, user)` before next CLI command. This
   matters for both CLI and WebUI hostname flows.
6. C1111-4P `Gi0/1/0..Gi0/1/3` are L2 switchports by default. IOS
   rejects `ip address` on a switchport. `set_interface_ip` prepends
   `no switchport` to handle this — safe no-op on routed ports.
7. `python -O` strips `assert` statements. Use explicit runtime
   checks for invariants you actually need.
8. ChromaDB defaults to L2 distance. For cosine-normalised MiniLM
   embeddings, set `metadata={"hnsw:space": "cosine"}` at collection
   creation.
9. RAG deps (`chromadb` / `sentence-transformers` / `torch`) are
   import-lazy via a wrapper in `tool_registry.py` — importing the
   registry doesn't drag torch into every process. Saves ~10s per
   pytest run.
10. WS handlers must race `q.get()` with `ws.receive_text()` to
    detect idle client disconnects. Otherwise dead subscribers pile
    up across page navs.
11. **Environment**: the shell has an empty `ANTHROPIC_API_KEY=`
    exported somewhere (likely `.bashrc`) which shadows the value in
    `.env`. Pydantic Settings prefers process env over `env_file`.
    Workaround: launch uvicorn with `Remove-Item Env:ANTHROPIC_API_KEY`
    (PowerShell) or `unset ANTHROPIC_API_KEY` (bash) first. Real fix
    is to remove the empty export from the shell profile.

USER PROFILE (from memory):
- Filip knows Cisco config deeply — use Cisco terms freely (VLAN, VTY,
  `ip http server`, IOS XE, TextFSM, switchport vs routed, etc.).
- Software tooling is new — on first mention of any library/framework,
  give a one-sentence plain-English explanation, then continue.
- Filip prefers terse responses; he can read the diff.

USER PREFERENCES:
- Speak Slovak in chat scenarios (Claude Haiku does this naturally per
  the planner's system prompt).
- Talk to him in English for development discussion.
- Tags are hands-off by default per `CLAUDE.md` — Filip creates
  milestone tags manually. Backup tags can be created with explicit
  per-instance permission.

WHAT'S NEXT — Day 8: alpha freeze + GUI completion → `v0.4.0-alpha.1`
+ `release/alpha-1-freeze` branch + GitHub Release. Per
`PROJECT_PLAN.md §7 Day 8`:

- `scripts/run_smoke_tests.py` must pass 5× in a row clean against
  the cabled C1111 with `SMOKE_ALLOW_WRITES=1 SMOKE_HEADLESS=1`.
- Frontend pages to ship: Logs (real `/api/logs/recent` view with
  filters), Backups (list snapshot folders + restore-from-one with
  confirmation), Devices (the single C1111 with live status +
  reachability check). The sidebar items were removed today (they
  were dead links); they come back as real pages.
- 🎯 At this point the project passes the grading floor.

OUTSTANDING FROM TODAY:
- VLAN WebUI flow's Name-field selector was tuned today but needs one
  more real-router test to confirm (the previous failure showed VLAN
  created but with empty Name). The new selector chain leads with
  `data-ng-model` / `id` / `placeholder` CSS; verify it picks up the
  Name field correctly.
- WebUI help guide ingest — Filip's previous mirror attempt failed.
  Plan in `docs/day7-summary.md` has a 4-phase diagnostic-first
  approach. Optional side-track; would push RAG smoke 7/10 → 9/10.

READ THESE FIRST (in order):
1. `CLAUDE.md` — quick rules for the coding agent.
2. `PROJECT_PLAN.md §7` — the 10-day plan, Days 1–7 marked DONE.
3. `docs/day7-daily-log.md` — what shipped today (this file's sibling),
   with all 21 commits and the 3 UX rounds explained.
4. `docs/how-it-works.md` — plain-English technical overview.

To start: say "start Day 8" and I'll begin with the alpha-freeze smoke
loop and the three missing frontend pages.

---
