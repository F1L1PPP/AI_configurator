# Day 1 Kickoff Prompt — Cisco AI Config Agent

> Paste the block below into a fresh Claude Code session that has this repo open. It is written in my voice, addresses the cables-not-available constraint, and tells the agent exactly what is in/out of scope for today.

---

## START DAY 1 — bootstrap track only (router pre-flight deferred)

Read `PROJECT_PLAN.md` and `CLAUDE_INSTRUCTIONS.md` in the repo root before doing anything else. Those two files are the source of truth. If anything I say below contradicts them, surface the conflict — don't silently choose.

### Context for today

I do **not** have the C1111 with me right now. I forgot the Ethernet patch cable and the console (rollover) cable at home. The router itself is healthy — I tested SSH + HTTPS reachability earlier this week and it answered fine, so there is no hardware doubt. This means today is a **pure local bootstrap day**: nothing that touches the router, no SSH probes, no Playwright login attempts, no `show` commands, no snapshot to USB. All of those move to the next session in which I have the cables.

Design references (mockups + the four independent AI plans I compared) live in `C:\AI_configurator_files\` — the same folder I attached to this project earlier. The relevant ones are the two `ChatGPT Image 11. 5. 2026 …png` mockups (Sidebar / TopBar / mesh sphere / WebUI Agent Live screen), `cisco-ai-config-design-screens.html` (interactive mockup), and the four PDF plans (Claude Sonnet, Gemini 3.1 Pro, ChatGPT 5.5 Deep Search, Perplexity). Treat these as design + decision context only — they have already been distilled into `PROJECT_PLAN.md §11`. Do not re-litigate the stack.

### Scope for this session — what to ship

Pull from `PROJECT_PLAN.md §7 Day 1`, drop the router items, keep the rest. Concretely:

1. **Repo skeleton** matching the layout in `PROJECT_PLAN.md §5`. Create every directory listed (empty `.gitkeep` is fine where there's no code yet), but only *populate* the files needed for the tag below.
2. **Hygiene files**: `.gitignore` (must exclude `.env`, `artifacts/`, `logs/`, `vectorstore/`, `screenshots/`, `backups/`, `__pycache__/`, `node_modules/`, `.venv/`, `.pytest_cache/`, `*.pyc`, `.ruff_cache/`), `.env.example` (every key referenced by `backend/core/settings.py`, no real values), `README.md` (install steps from a clean Windows + how to run lint/tests — short, no marketing fluff).
3. **`pyproject.toml`** with: Python 3.12 target, project metadata, `[tool.ruff]` config (line length 100, target-version py312, sensible default rule set incl. `E`, `F`, `I`, `B`, `UP`, `SIM`), and `[tool.pytest.ini_options]` (testpaths = `tests`, addopts for `-ra --strict-markers`). `requirements.txt` pinned for the libraries we actually import today — do not pull in Netmiko/Playwright/Chroma/etc. yet, those land on the days they're used. For Day 1: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `structlog`, `python-dotenv`, plus dev deps `ruff`, `pytest`, `pytest-asyncio`.
4. **Pydantic Settings** at `backend/core/settings.py` — typed `Settings` class loading from `.env` with: `ANTHROPIC_API_KEY`, `ROUTER_HOST`, `ROUTER_SSH_USER`, `ROUTER_SSH_PASSWORD`, `ROUTER_WEBUI_USER`, `ROUTER_WEBUI_PASSWORD`, `ROUTER_WEBUI_BASE_URL`, `LOG_LEVEL`, `ARTIFACTS_DIR`, `LOGS_DIR`. Use `SettingsConfigDict(env_file=".env", extra="ignore")`. Provide a `get_settings()` accessor with `@lru_cache`. **Do not** read any of these secrets in code paths today — we're just wiring the loader.
5. **Structured logging** at `backend/core/logging.py` — `structlog` JSONL configuration writing to `logs/actions.log` plus stderr in dev. Include a `redact_secrets` processor stub (real redaction comes Day 2 with the Netmiko session log) that already drops `password`, `secret`, `api_key`, `token` keys from event dicts. Expose `configure_logging()` and `get_logger(name)`.
6. **FastAPI hello-world** at `backend/main.py` — one `GET /healthz` returning `{"status":"ok"}`. Call `configure_logging()` on startup. That's it; no routes from `PROJECT_PLAN.md §5` yet.
7. **Next.js scaffold** under `frontend/` — `create-next-app` with TypeScript, Tailwind, App Router, no custom server. Strip the boilerplate landing page down to a single `/` route that renders `<main>Cisco AI Config Agent — bootstrap</main>`. No design system work today (that's Day 11). Verify it builds (`npm run build`) before committing.
8. **`CLAUDE.md`** at repo root — short, concrete, per `PROJECT_PLAN.md §9`. No more than ~40 lines. The long-form rules already live in `CLAUDE_INSTRUCTIONS.md`; `CLAUDE.md` is the in-repo summary the coding agent actually reads.
9. **Checkpoint skill**: `.claude/skills/checkpoint/SKILL.md` + `scripts/checkpoint.sh` + `scripts/checkpoint.ps1` (Windows is my dev box). The skill runs: `ruff check`, `pytest -q`, `git add -A`, conventional commit message argument, `git push`, and an annotated daily tag `backup-YYYYMMDD-HHMM`. If lint or tests fail, abort before commit.
10. **CI**: `.github/workflows/ci.yml` running `ruff check` and `pytest -q` on push and PR against `main` and `develop`. Use `actions/setup-python@v5` pinned to 3.12, cache pip. Frontend lint/build can wait — backend is the value path. Add a stub `nightly-backup.yml` workflow that fires on `schedule` but only logs "nightly backup placeholder" for now; we wire real artifact upload on Day 12.
11. **Smoke test for the bootstrap itself**: one `tests/unit/test_settings.py` that imports `get_settings()` with a dummy `.env` and asserts the fields are typed correctly. One `tests/unit/test_health.py` that uses `httpx.AsyncClient` against the FastAPI app and asserts `/healthz` returns 200. This proves CI is actually doing something on Day 1.

### Explicitly **out of scope** today (deferred to the cabled session)

These come from `PROJECT_PLAN.md §3` and `§12` and require the router:

- §3 pre-flight checklist (privilege 15 user, `ip http server`, `ip http secure-server`, `ip http authentication local`, 30 VTY lines, `ip ssh version 2`, manual WebUI walk to Configuration → VLANs and Administration → Device Properties, recording the WebUI version into `docs/router-prerequisites.md`)
- Exporting the known-good `running-config` to USB (the bricking-guard backup)
- Any throwaway SSH/WebUI reachability probes

When I'm back at the router, I'll start a new session with "resume Day 1 — router pre-flight" and we close those items before tagging.

### Tagging policy for today

Per `CLAUDE_INSTRUCTIONS.md` hard rule #7, I create tags, not you. **Do not tag `v0.0.1-bootstrap` today** — the tag's definition in `PROJECT_PLAN.md §6.2` is "Repo skeleton, CI, Pydantic settings, logging, **prerequisites verified**", and the prerequisites are not verified yet. When pre-flight closes, I'll create the tag manually. For today, end on a clean commit on `feature/bootstrap` pushed to origin, plus the daily `backup-YYYYMMDD-HHMM` tag via `/checkpoint`.

### Branching for today

Work on `feature/bootstrap` off `develop`. If `develop` doesn't exist yet, create it from `main` first. Direct commits to `main` are forbidden (hard rule #8).

### Workflow expectations

- Conventional Commits, one logical unit per commit. I expect roughly: `chore: scaffold repo layout`, `chore: add gitignore and env example`, `build: configure ruff and pytest`, `feat(core): add pydantic settings loader`, `feat(core): add structlog config`, `feat(api): add healthz endpoint`, `feat(frontend): scaffold next.js app`, `docs: add CLAUDE.md`, `chore(ci): add ruff+pytest workflow`, `feat(skills): add checkpoint slash-command`, `test: cover settings and healthz`. Roughly 8–11 commits is fine; one 200-line megacommit is not.
- Run `ruff check` and `pytest -q` before every commit. If either fails, fix before committing.
- Before you start, give me a one-screen plan: ordered task list, estimate per task, total estimate with confidence band. I will say "go" or push back. **Do not start implementation before I confirm.**
- If any directory or file from `PROJECT_PLAN.md §5` is unclear, ask **one** clarifying question — not three.
- At the end of the session, summarize in ≤10 lines: what shipped, what's open, what's next (which is: cabled pre-flight + manual `v0.0.1-bootstrap` tag).

### One nudge on the frontend

The `frontend/` directory exists in `§5` but Week 1 is backend-heavy. Do the minimal `create-next-app` scaffold + healthz proof that the dev server runs, then move on. The real design tokens, mesh sphere, sidebar, and WebUI Agent Live screen are Days 11–13 work and rely on the mockups in `C:\AI_configurator_files\`. Don't pre-empt them today.

Confirm scope, give me the plan + estimate, and wait for my "go."
