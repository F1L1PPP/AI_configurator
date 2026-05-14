# Next-session kickoff prompt

Copy everything between the `=== START ===` and `=== END ===` markers
below and paste it as the very first message in a fresh Claude Code
chat opened at the repo root. It tells the new chat where the active
plan lives, what state the project is in, and what task to pick up.

When you paste, make sure plan mode is OFF (the kickoff doesn't need
re-planning — the plan already exists). The new chat should read the
referenced files, confirm understanding, and then wait for your
specific instruction (e.g. "start Phase 2" or "show me Phase 3 code").

---

```
=== START ===

You're picking up the Cisco AI Config Agent project mid-flight. Before
doing anything, read these files in order:

1. CLAUDE.md (repo root) — workflow, branch, commit, tag rules. ALL of
   them apply. The locked stack is non-negotiable.

2. docs/plan-ai-first-webui.md — the active plan (the "AI-first
   WebUI configuration v0.4.0" shift). You are starting at the
   beginning of **Phase 2** ("Decide what stays, what moves to
   history"). Phases 0 (milestone + backup tags) and 1 (prompt
   caching + token telemetry + RAG cost hints) are already done.

3. docs/how-it-works.md — high-level architecture, if you need to
   orient yourself.

4. (Optional but useful) docs/rag-sources.md for the RAG corpus
   state.

## Project state at the start of this chat

- Branch: feature/bootstrap
- Last commit (origin): 914419b — feat(planner): prompt caching +
  token telemetry + RAG cost hints
- Latest milestone tag: v0.3.2-webui-flows-working (commit b084088)
  — clean rollback point; this is where the WebUI flows first
  worked end-to-end on real hardware.
- Latest backup tag: backup-20260514-0935
- Tests green: 303 passing (unit + integration). Smoke tests skip
  without ROUTER_HOST in .env.
- Lint/types green: ruff, ruff format, mypy, tsc --noEmit all clean.
- WebUI flow proved end-to-end against the real C1111 today
  (VLAN 46 'IOT' added, verified, marked EXECUTED).

## What's next

**Phase 2** of the plan (~docs/plan-ai-first-webui.md §"Workstream 2 →
Phase 2"). This is docs-only — no code change. Goal: capture the
"keep / archive" matrix as a real README in backend/webui_agent/ so a
future developer (or AI assistant) walking into this codebase
understands the architecture decision.

Specifically:
- Read the plan's Phase 2 keep/archive table.
- Create backend/webui_agent/README.md with that matrix, plus a
  one-paragraph "why subprocess isolation" explainer pointing at
  _playwright_subprocess.py.
- Commit with a `docs(webui-agent):` Conventional Commit message.
- Push.

After Phase 2, Phase 3 (semantic_dom.py — the actual code start of
the AI-first shift) is the next real engineering step.

## Operating notes

- Filip works on Windows. Python 3.13 (system) with a venv at
  C:\GIT\AI_configurator\.venv (main checkout) or inside a worktree
  under .claude/worktrees/<name>/.venv. The latter has the latest
  packages installed; prefer it if the agent is operating in a
  worktree.
- Backend: `uvicorn backend.main:app --reload`, port 8000.
- Frontend: `npm run dev` from `frontend/`, port 3000.
- Approval flow is INLINE in chat (APPROVE / EXECUTE NOW buttons
  appear under the agent's reply). No /preview round-trip.
- The Playwright child process pattern is load-bearing on Windows.
  See backend/webui_agent/_playwright_subprocess.py + _subprocess.py.
- Never call mypy with `|| true` — it's gated in CI now.
- Conventional Commits required. `ruff check` + `mypy` + `pytest -q`
  before every commit; the pre-commit hook will catch you if you
  skip ruff.
- Tags are hands-off unless Filip explicitly authorises a new one.
  The plan's Phase 7 will need a v0.4.0 tag — wait for the go.

## What I want from you to start

Confirm you've read the plan and CLAUDE.md, then summarise back in
3–5 sentences:

1. What just shipped (Phase 0 + Phase 1).
2. What Phase 2 asks for.
3. Where to write the new README.
4. What you'll touch and what you won't.

Then wait for "go" before making any change. Don't propose
re-planning the architecture — the plan is locked unless I ask.

=== END ===
```

---

## Notes for the future

- The plan itself (`docs/plan-ai-first-webui.md`) is the canonical
  forward-looking document. Update it as phases land, or archive it
  to `docs/history/` once v0.4.0 ships.
- The original planning artifact lived at
  `C:\Users\filip\.claude\plans\plan-what-would-be-enchanted-swan.md`
  during the session that authored it. That file is local to one
  Claude Code workspace and not version-controlled; the canonical
  copy is the one in `docs/` now.
- If the kickoff prompt above gets stale (e.g. Phase 2 lands and the
  next chat should pick up Phase 3), edit the "## What's next"
  section of this file before pasting it.
