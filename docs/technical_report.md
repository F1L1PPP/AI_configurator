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

- **3.1 Principle.** Why hybrid (LLM tool-use + deterministic Python flows)
  instead of pure autonomous browser agents — 14-day timeline + safety
  guarantees rule out autonomous writes.
- **3.2 Stack.** Table from `PROJECT_PLAN.md §4.2`. One-line justification per row.
- **3.3 Hybrid execution model.** Numbered walk-through of a single user request
  from natural language → orchestrator → tool pick → snapshot → preview → approval
  → execute → verify → evidence (the 10-step list from §4.3).
- **3.4 Component diagram.** Insert ASCII or `mermaid` diagram: User → Frontend
  → FastAPI → Orchestrator → (CLI agent, WebUI agent, RAG) → Cisco C1111.

## 4. The six scenarios (2 pages — main demo evidence)

For each of the six §2 scenarios (see `docs/smoke-scenarios.md`): the prompt
sent, the tool the orchestrator picked, the action_id approval gate, the
verification call, and the resulting evidence artefact paths
(`artifacts/screenshots/<session>/*.png`, `artifacts/device-snapshots/*.cfg`,
`artifacts/reports/*.json`).

Include one screenshot per scenario from the alpha-freeze smoke run (Day 9).

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

## 6. RAG evaluation (½ page)

Document corpus (5–10 curated Cisco PDFs scoped to VLANs / hostname / WebUI
nav / interfaces, sourced from `docs/rag-sources.md`). Chunking parameters
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

Pointer to the alpha-freeze artifact bundle (cut by Day 9
`scripts/run_smoke_tests.py`):
- 5 runs × 6 scenarios = 30 screenshot sets
- 30 pre/post device snapshots
- 30 Playwright traces
- 30 structured execution reports

Plus the 10–15 min demo video and the `release/alpha-1-freeze` branch hash —
proof the project is reproducible from a single git checkout.

## 9. Repository & install (½ page — appendix)

`PROJECT_PLAN.md §5` repo structure summary. Install steps from a clean
Windows machine (3 commands from `README.md`). Required env vars
(`.env.example`). How to run lint + tests + smoke harness.

---

**Status:** outline only. Sections 1, 2, 3 land Day 4 (architecture); §4, §5,
§8 are filled in as scenarios ship Days 2–8; §6 lands Day 7; §7 stays current
in `PROJECT_PLAN.md §10` and gets transcribed on Day 10; §9 is mechanical.
