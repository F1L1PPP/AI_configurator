---
name: dead-code-audit
description: >-
  Read-only, multi-agent (Opus 4.8) audit of whether every piece of code in the project is actually NEEDED — finds dead/unused/orphaned files, functions, classes, routes, and config, then an adversarial verify pass kills false-positives. Zero edits by construction (Explore agents have no write tool). Invoke when the Director asks to "check if everything in the code is needed", to find dead/unused/orphaned code, to run a code-necessity or cleanup audit, or before a dead-code sweep.
---

# Dead-code / code-necessity audit

A read-only, fan-out audit answering one question: **is every file / function / route / config actually used?** Born 2026-05-30 — 25 Opus 4.8 agents found **60 confirmed-dead** items while the verify stage caught **11 false-positives**. The standing findings live in `docs/dead-code-findings.md`.

## The pattern — find → adversarially verify → synthesize
1. **Slice** the codebase by area: one slice per backend subpackage (`webui_agent`, `orchestration`, `api`, `cli_agent`, `core`+`main`+`db`, `knowledge_agent`), plus `frontend`, each backup/sandbox tree (e.g. `frontend-design-backup`, `playwright_playground`), `scripts`+`tools`, the selector YAML, and `tests`.
2. **Find** — one Opus `Explore` agent per slice. Flag SUBSTANTIVE items (whole files, public functions/classes, routes, YAML sections) that are *defined but never referenced*. Every candidate must be grepped **repo-wide for callers BEFORE flagging**. Cap ~15 best candidates per slice.
3. **Verify** — one Opus `Explore` agent per slice. Adversarially try to **PROVE each candidate IS used**: dynamic import / `getattr` / `importlib` / reflection; FastAPI `include_router` / `app.mount` / decorator registration; `pyproject`/console-scripts/Makefile/CI entry points; test-only usage; config-driven dispatch (YAML key → code); `__all__` / re-exports; string references; frontend `index.html <script>` / `app.jsx` imports; conftest fixtures by name. `confirmedDead` only if no usage survives.
4. **Synthesize** — one Opus agent compiles an advisory report grouped by area, ordered by impact (whole trees/files first).

## Load-bearing rules (learned the hard way)
- **`agentType: 'Explore'`** for every agent — it has no Edit/Write tool, so the audit *physically cannot* change code. Read-only by construction (matches a "do not edit anything" directive).
- **`model: 'opus'`** (4.8) on every agent — necessity reasoning is subtle.
- **Adversarial verify is mandatory.** Dead-code audits are false-positive-prone: ~11 of 71 candidates here were actually live (a serialized API field, a `raise`d-but-never-`except`ed exception, a `__main__` entry point). Skipping verify ships a wrong/noisy list.
- **Scope every grep to the TARGET worktree.** This machine has multiple git worktrees with stale copies — searching the wrong one gives false negatives. Put the absolute worktree ROOT in every prompt and forbid searching elsewhere.
- **Classify, don't just flag:** *runtime-dead* (no production path) ≠ *test-coupled* (a passing test asserts it → must move WITH its test) ≠ *live*. Report all three so the Director can act safely.
- **Advisory only — never delete.** The skill produces `docs/dead-code-findings.md`; removal is a separate, Director-approved *editing* task.

## How to run
Invoke the `Workflow` tool with a `pipeline(AREAS, findStage, verifyStage)` + synthesis script: each stage is `agent(prompt, { phase, model:'opus', agentType:'Explore', schema })`. A reference script was persisted as `dead-code-audit-*.js` under the session's `workflows/scripts/` dir — re-author from the pattern above if it's gone. ~2×(#areas)+1 Opus agents (here: 12+12+1 = 25). Write the synthesized report into `docs/dead-code-findings.md` as a deferred-cleanup checklist; never delete in the same pass.

## Reference
- `docs/dead-code-findings.md` — the current findings (deferred-cleanup checklist).
- `director-blueprint` — the Opus/Sonnet/Haiku roles + audit discipline this leans on.
