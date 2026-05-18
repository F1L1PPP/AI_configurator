# Cisco AI Config Agent — Technical Report

> Source for the ≥8-page PDF deliverable (assignment §1). Section headers + 1-line
> placeholders are the skeleton; each section gets fleshed out as the relevant day's
> work lands. PDF render on Day 10 via `pandoc` or the `docx`/`pdf` skill.

---

## 1. Abstract (½ page)

One paragraph: the project goal (AI agent that configures a Cisco C1111 via CLI
and WebUI under human-in-the-loop approval), the methodology (LLM plans, Python
executes deterministically), and the final result (alpha freeze on Day 9, all six
§2 scenarios passing 5× in a row against the real router).

## 2. Assignment requirements & grading map (½ page)

Direct table from `PROJECT_PLAN.md §1` — grading area → weight → what unlocks the
points → where in this report the evidence lives. Confirms every gradeable item
has matching evidence in `artifacts/`.

## 3. Architecture (1½ pages)

The decision in one line: **LLM plans. Python executes.** Subsections:

- **3.1 Principle.** AI-first generic configure model: an outer Haiku 4.5
  tool-use loop picks a `propose_*` tool, an inner Haiku step planner drafts
  the plan grounded by RAG and (for WebUI) [`describe_page()`](../backend/webui_agent/semantic_dom.py),
  and a deterministic per-step Python click/CLI executor runs under
  human-in-the-loop approval. Pure autonomous browser writes are ruled out by
  the safety guarantees; the LLM never directly drives the router.
- **3.2 Stack.** Table from `PROJECT_PLAN.md §4.2`. One-line justification per row.
- **3.3 Hybrid execution model.** Numbered walk-through of a single user request
  from natural language → outer planner tool pick → snapshot → inner planner
  drafts step list → preview → approval → per-step execute → verify → evidence
  (the 10-step list from §4.3, updated for the inner-planner pattern).
- **3.4 Component diagram.** Insert ASCII or `mermaid` diagram: User → Frontend
  → FastAPI → Orchestrator (outer Haiku) → inner Haiku planners
  ([configure_planner](../backend/orchestration/configure_planner.py) /
  [cli_configure_planner](../backend/orchestration/cli_configure_planner.py))
  → (CLI agent, WebUI [generic_driver](../backend/webui_agent/generic_driver.py),
  RAG [retrieve](../backend/knowledge_agent/retrieve.py)) → Cisco C1111.
- **3.5 Generic configure path.** The `propose_webui_configure` and
  `propose_cli_configure` tools delegate plan-drafting to the inner Haiku
  planner, which is grounded by RAG snippets and a token-bounded
  `describe_page()` JSON view of interactive elements. The legacy hand-coded
  POM fast paths in [backend/webui_agent/pages/](../backend/webui_agent/pages/)
  + [flows/](../backend/webui_agent/flows/) wrappers and
  [cli_agent/write_tools.py::set_*](../backend/cli_agent/write_tools.py) are
  retained as deterministic fast paths for hostname/VLAN where they already
  ship green.

## 4. The six scenarios (2 pages — main demo evidence)

For each of the six §2 scenarios (see `docs/smoke-scenarios.md`): the prompt
sent, the tool the orchestrator picked, the action_id approval gate, the
verification call, and the resulting evidence artefact paths
(`artifacts/screenshots/<session>/*.png`, `artifacts/device-snapshots/*.cfg`,
`artifacts/reports/*.json`).

Beyond the original alpha-1 six (CLI shows ×3, CLI write hostname,
CLI write interface-IP, WebUI hostname, WebUI VLAN), this section also
covers the AI-first scenarios proven post-alpha-1, each carrying the same
evidence trail (action_id approval, pre/post device snapshots, pre/post
screenshots, structured execution report):

- **WebUI static route** via `propose_webui_configure` (inner planner +
  generic driver).
- **WebUI OSPF** via `propose_webui_configure`.
- **WebUI ISIS** via `propose_webui_configure` — the Angular modal-race
  was fixed in `v0.4.0-alpha.4-settle-wait` via
  [`_settle_page()`](../backend/webui_agent/_playwright_subprocess.py);
  end-to-end verification is currently blocked on transient Anthropic 529s
  at write time and remains an open item.
- **CLI generic** via `propose_cli_configure` (inner CLI planner +
  per-line executor).

Include one screenshot per scenario from the latest rolling-alpha smoke run.

## 5. Safety model (1 page)

The four hard guarantees from `CLAUDE_INSTRUCTIONS.md` hard rules 2–7, with
implementation pointers:

- **HITL approval gate** — every write tool requires a server-side `action_id`
  approved via `POST /api/approve/{action_id}`. Cannot be overridden in the
  prompt (`backend/orchestration/confirmations.py`).
- **Pre-snapshot mandate** — `show running-config` + `show version` + `show ip
  interface brief` saved to `artifacts/device-snapshots/` before every write.
- **Screenshot evidence** — Playwright steps screenshot pre/post; on error save
  DOM dump + Playwright trace too. No auto-retry on write failure.
- **Secret redaction** — structlog `redact_secrets` processor drops `password`,
  `secret`, `api_key`, `token` keys; Netmiko session log uses `no_log` filter.
- **Bricking guard** — known-good `running-config` exported to USB before Day 3.
- **Inner planner cannot bypass approval** — the inner Haiku call only drafts
  the step plan; the approval gate sits at the OUTER tool boundary.
  `propose_*_configure` returns the plan and an `action_id`; only
  `execute_tool` dispatches writes, and only after the state machine
  transitions to `EXECUTING` atomically. The TOCTOU window is closed and
  regression-tested by
  [`tests/unit/test_routes_execute_toctou.py`](../tests/unit/test_routes_execute_toctou.py).

## 6. RAG evaluation (½ page)

Document corpus: Cisco IOS XE WebUI guide + companion docs, indexed as 913
chunks in ChromaDB (sourced from `docs/rag-sources.md`). Chunking parameters
(~500 tok, 50-tok overlap, heading-aware). Embedding model
(`sentence-transformers/all-MiniLM-L6-v2`). Retrieval test: 10 hand-graded
queries, ≥7/10 passes. Sources cited in every Chat reply.

## 7. Risks, limitations, mitigations (1 page)

Top six from `PROJECT_PLAN.md §10` risk register: WebUI prerequisites missing,
WebUI selectors break across IOS XE versions, self-signed cert, WebUI session
timeout (5 min idle), RAG retrieves irrelevant chunks, AI sprawl in commits.
For each: probability, impact, mitigation that's actually wired into the code
(not just listed in a doc).

## 8. Demo evidence (½ page)

Pointer to the rolling-alpha artifact trail produced by the working
[`tests/smoke/`](../tests/smoke/) harness. The current tag chain
(`v0.4.0-alpha.1` → `…-alpha.4-settle-wait`, with `…-alpha.4-pre-redesign`
as the pre-redesign freeze) carries:
- per-scenario screenshot sets (`artifacts/screenshots/<session>/*.png`)
- pre/post device snapshots (`artifacts/device-snapshots/*.cfg`)
- Playwright traces on failure
- structured execution reports (`artifacts/reports/*.json`)

The 5 runs × 6 scenarios = 30 artefacts target remains the aspirational
alpha-1 consolidation goal; the demo video and a formal `release/alpha-1-freeze`
tag are produced once that consolidation lands.

## 9. Repository & install (½ page — appendix)

`PROJECT_PLAN.md §5` repo structure summary. Install steps from a clean
Windows machine (3 commands from `README.md`). Required env vars
(`.env.example`). How to run lint + tests + smoke harness.

---

**Status:** outline only. Sections 1, 2, 3, 5, 6, 9 are current as of the
rolling-alpha line (`v0.4.0-alpha.4-settle-wait`); §4 and §8 get fleshed
out once the AI-first scenarios bake in (post-alpha-1 consolidation); §7
stays current in `PROJECT_PLAN.md §10` and gets transcribed at render
time; PDF render is post-alpha-1-consolidation.
