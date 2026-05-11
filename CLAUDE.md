# CLAUDE.md — Cisco AI Config Agent

Full rules: `CLAUDE_INSTRUCTIONS.md`. This file is the quick-ref the coding agent reads first.

## Branches
- Always work on `feature/*` off `develop`. Never commit directly to `main`.
- Merge path: `feature/*` → `develop` → `main`.

## Tags — hands-off
- Never create, move, or delete tags. Never touch `release/alpha-1-freeze`.
- Tags are created manually by me on milestone days.

## Before every router write
1. Take a device snapshot (`show running-config`, `show version`, `show ip interface brief`) → `artifacts/device-snapshots/`.
2. Every write requires server-side approval via `/api/approve/{action_id}` — no prompt override.
3. On WebUI: screenshot before and after every Playwright step → `artifacts/screenshots/<session>/`.
4. If a write fails: stop, save evidence, surface to me. Never auto-retry.

## Tone — explain software tools simply
- Filip knows Cisco config deeply — use Cisco terms freely (VLAN, VTY, `ip http server`, IOS XE, TextFSM, etc.).
- Software tooling is new to him — on first mention of any library/framework/concept (FastAPI, Pydantic, Tailwind, hooks, decorators, CORS, etc.), give a one-sentence plain-English explanation of what it IS, then continue. After the first explanation in a session, use freely.

## Commits
- Conventional Commits only: `feat(cli-agent): …`, `fix(webui): …`, `docs: …`, `test: …`, `chore: …`.
- Run `ruff check` + relevant pytest before every commit. Fix failures before committing.
- Commit at every logical unit or every 60–90 min, whichever comes first.
- Push after every green lint+test run.

## Never commit
`.env`, credentials, `artifacts/`, `logs/`, `vectorstore/`, `screenshots/`, `backups/`, `__pycache__/`, `node_modules/`, `.venv/`.

## End of day
Run `/checkpoint` → lint + test + commit + push + annotated daily tag `backup-YYYYMMDD-HHMM`.

## Models
- Opus 4.7: architecture, hard bugs, release-gate review.
- Sonnet 4.6: bulk implementation, tests, page analysis.

## Stack (locked — don't change without approval)
Python 3.12 · FastAPI · Pydantic Settings · structlog · Netmiko · Playwright · ChromaDB ·
sentence-transformers/all-MiniLM-L6-v2 · Anthropic SDK (no LangChain) · Next.js 14 + TypeScript + Tailwind
