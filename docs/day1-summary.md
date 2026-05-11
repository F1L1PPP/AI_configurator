# Day 1 — Summary of work prepared

**Date:** 2026-05-11
**Branch:** `feature/bootstrap` (off `develop`)
**Daily backup tags:** `backup-20260511-1029`, `backup-20260511-1117`, plus today's final
**Status:** local-only track complete; router-dependent items deferred to the cabled
session ("resume Day 1 — router pre-flight").

---

## What shipped (in order)

### 1. Repository skeleton (`chore: scaffold repo layout`)

Full `PROJECT_PLAN.md §5` directory tree — 38 directories with `.gitkeep`
markers. Every future component (CLI agent, WebUI agent, RAG, screenshots,
traces, snapshots, reports) has a home reserved so future commits don't
need to invent the tree.

### 2. Hygiene files (`chore: add gitignore and env example`)

- `.gitignore` — excludes secrets (`.env`), runtime artifacts (`artifacts/`,
  `logs/`, `backups/`, `vectorstore/`), and build junk (`node_modules`,
  `.venv`, `__pycache__`).
- `.env.example` — template with all 10 keys the app needs, placeholder values.
- `README.md` — install steps from a clean Windows machine.

### 3. Build configuration (`build: configure ruff and pytest`)

- `pyproject.toml` — Ruff (line 100, py312 target, rules E/F/I/B/UP/SIM) +
  pytest (testpaths=tests, `-ra --strict-markers`, asyncio_mode=auto).
- `requirements.txt` — Day-1-only pinned deps: fastapi, uvicorn, pydantic,
  pydantic-settings, structlog, python-dotenv + dev: ruff, pytest,
  pytest-asyncio, httpx, pre-commit. Netmiko / Playwright / ChromaDB land on
  the day they're first imported.

### 4. Pydantic Settings (`feat(core): add pydantic settings loader`)

`backend/core/settings.py` — typed `Settings` class with all 10 config keys
(Anthropic key, router SSH credentials, router WebUI credentials, log level,
artifact + log paths). `@lru_cache`d `get_settings()` accessor.
`SettingsConfigDict(env_file=".env", extra="ignore")`.

### 5. Structured logging (`feat(core): add structlog config`)

`backend/core/logging.py` — JSONL output to both `logs/actions.log` and
stderr. `redact_secrets` processor drops `password`, `secret`, `api_key`,
`token` keys from any event dict. `configure_logging()` and `get_logger(name)`
exported.

### 6. FastAPI hello-world (`feat(api): add healthz endpoint`)

`backend/main.py` — FastAPI app, lifespan calls `configure_logging()` on
startup. Single route `GET /healthz` returns `{"status":"ok"}`.

### 7. Frontend scaffold (`feat(frontend): scaffold next.js app`)

Next.js 14 + TypeScript + Tailwind + App Router via `create-next-app`,
stripped to a minimal bootstrap page. `npm run build` passes. Component
subdirectories (`components/layout/`, `chat/`, `agent/`, `preview/`,
`webui-agent/`, etc.) created with `.gitkeep` markers.

### 8. CLAUDE.md (`docs: add CLAUDE.md`)

37-line quick-reference for the coding agent: branching rules (feature/*
only, never `main`), tag policy (Filip creates, AI never moves),
write-before-router rules (snapshot first, approval required, no auto-retry),
commit style (Conventional Commits, ruff+pytest before every commit), stack
lock-in. Long-form rules live in `CLAUDE_INSTRUCTIONS.md`.

### 9. CI workflows (`chore(ci): add ruff+pytest workflow and nightly backup stub`)

- `.github/workflows/ci.yml` — runs `ruff check` + `pytest -q` on Python 3.12
  for every push to `main`/`develop`/`feature/**` and every PR. `actions/setup-python@v5`
  with pip cache.
- `.github/workflows/nightly-backup.yml` — stub that fires at 02:00 UTC daily
  and logs a placeholder. Real artifact upload wires on Day 12 of the original
  plan (now deferred — see §3 below).

### 10. Checkpoint slash-command (`feat(skills): add checkpoint slash-command`)

`.claude/skills/checkpoint/SKILL.md` + `scripts/checkpoint.ps1` (Windows) +
`scripts/checkpoint.sh` (POSIX). Daily save point: lint → tests → commit →
push → annotated `backup-YYYYMMDD-HHMM` tag. Aborts before `git add` if lint
or tests fail.

### 11. Unit tests (`test: cover settings and healthz`)

`tests/unit/test_settings.py` (3 tests — typed env load, defaults, cache) +
`tests/unit/test_health.py` (1 test — `httpx.AsyncClient` + `ASGITransport`,
asserts `/healthz` returns `200 {"status":"ok"}`). 4 tests total, all green.

### 12. Plan revision A (`docs(plan): move webui work earlier and add day-3 probe`)

`PROJECT_PLAN.md §7` reshuffled. WebUI agent work pulled earlier (Days 4–5
discovery + hostname; Day 8 VLAN; Day 9 buffer) — was Days 7–9 in original.
Day 3 gets a 30-min Playwright cert/login probe. Risk register updated.

### 13. Plan revision B (`docs(plan): compress to 10 days with parallel gui track`)

Timeline 14 days/84h → 10 working days/60h (working week is 5 days). GUI
promoted from optional bonus to graded core deliverable per instructor —
now runs **in parallel** with backend (~1.5h/day from Day 1). Hard rule
added: if backend slips on a given day, that day's GUI is dropped.
Tag sequence compressed: `v0.4.0-alpha.1` Day 10 → Day 9,
`v1.0.0-demo` Day 14 → Day 10. Risk register entry "two weeks tight for
full GUI scope" replaced with "10 days tight for backend + parallel GUI" at
High/High severity.

### 14. GUI foundation (`feat(frontend): add design tokens, layout shell, dashboard with mocks`)

Matches the mockups in `C:/AI_configurator_files/`:
- Tailwind tokens — page/surface/sidebar backgrounds, full ink+rule grey
  scale, terminal accent colors, Inter (body) + Share Tech Mono (tech labels)
  via `next/font/google`.
- `frontend/components/layout/Sidebar.tsx` — 180px, Ethernet-port SVG logo +
  7 nav items (Dashboard / Devices / AI Agent / Configurations / Templates /
  Logs / Settings) with active-state left border, version footer.
- `frontend/components/layout/TopBar.tsx` — page title + breadcrumb +
  `AGENT ACTIVE` status pill.
- `frontend/app/layout.tsx` — Sidebar + TopBar shell wrapping `main`.
- `frontend/app/page.tsx` — Dashboard with three stat cards
  (Devices / Sessions / Actions), Recent Activity panel (4 mocked rows),
  Quick Actions panel (NEW AI SESSION / CONNECT DEVICE / VIEW LOGS /
  BACKUP CONFIG), Backend status panel.
- Removed Geist `.woff` files; switched to Google Fonts.
- Browser-tab title now `Cisco AI Config Agent` (was `Create Next App`).

### 15. Live `/healthz` wiring (`feat(frontend): wire dashboard backend status to /healthz`)

- `frontend/lib/api.ts` — `getHealth()` fetch helper, `API_BASE` env override.
- `frontend/components/status/BackendStatus.tsx` — client component polling
  `/healthz` every 5s, three states (checking / ok / down).
- `backend/main.py` — added FastAPI `CORSMiddleware` allowing
  `http://localhost:3000` and `:3001` origins.
- Dashboard's "Backend status" panel now uses the live component instead of
  the mocked badge.

### 16. Pre-commit hook (`chore: add pre-commit hook with ruff`)

- `.pre-commit-config.yaml` — runs `ruff --fix` before every commit.
- `requirements.txt` pinned `pre-commit==4.2.0`.
- `README.md` updated with `pre-commit install` step after `pip install`.
- Hook installed in this worktree's `.git/hooks/pre-commit`. Verified by
  running `pre-commit run --all-files` once — all files pass.

### 17. Day 10 documentation prep

- `docs/technical_report.md` — 9-section outline for the ≥8-page PDF
  deliverable, each section with 1–3 line content guide.
- `docs/smoke-scenarios.md` — all six §2 scenarios in prose with expected
  prompt → tool → verification → evidence. Day 9 smoke harness reads this;
  Day 10 tech report §4 cites it directly.
- `docs/day1-summary.md` — this file.

---

## What's open / deferred

### Router-dependent items (cabled session — "resume Day 1 — router pre-flight")

- Verify `PROJECT_PLAN.md §3` prerequisites on the C1111:
  - `privilege 15` user exists
  - `ip http server` + `ip http secure-server` enabled
  - `ip http authentication local`
  - `line vty 0 30` (30 VTY lines)
  - `ip ssh version 2`, `ip ssh time-out 60`
  - Management IP reachable (ping + SSH + HTTPS)
  - Manually walk WebUI to Configuration → VLANs and Administration →
    Device Properties
  - Record WebUI version in `docs/router-prerequisites.md` (selectors are
    version-sensitive)
- Export known-good `running-config` to USB (bricking guard)
- **You** create the `v0.0.1-bootstrap` tag manually after the above passes
  (hard rule #7 — I never tag)

### Doc not yet written

- `docs/router-prerequisites.md` — empty; gets filled in during cabled session
- `docs/rag-sources.md` — list of curated Cisco PDF URLs for Day 7 RAG ingest
- `docs/architecture.md` — Day 4 deliverable, after orchestrator design lands
- `docs/backup-policy.md` — Day 12 of original plan, may slip given the
  compression to 10 days

---

## What's verified working right now

- `python -m ruff check .` → all checks passed
- `python -m pytest -q` → 4 passed
- `python -m pre_commit run --all-files` → ruff hook passes
- `cd frontend && npm run build` → succeeds, 2 static pages, 88.8 kB
  first-load JS for `/`
- `uvicorn backend.main:app --reload` → starts, `GET /healthz` returns
  `{"status":"ok"}`, structured log written to `logs/actions.log`, CORS
  header `Access-Control-Allow-Origin: http://localhost:3000` present on
  responses
- Dashboard at `http://localhost:3000` (or `:3001`) renders Sidebar + TopBar
  + three stat cards + Recent Activity + Quick Actions + live Backend status
  panel that turns green when uvicorn is running and red when it's not

---

## What's next

### Immediately next session (whenever you have the cables)

"resume Day 1 — router pre-flight" — work through `PROJECT_PLAN.md §3`
checklist, USB-export running-config, fill in `docs/router-prerequisites.md`,
then create `v0.0.1-bootstrap` tag manually.

### Day 2 (after cabled session closes)

`PROJECT_PLAN.md §7 Day 2` — CLI read layer (Netmiko pool, `show_*` tools,
TextFSM parsing, action logger) + GUI Dashboard wired to real `/healthz`
(already done — bonus) + Recent Activity panel reads `logs/actions.log` for
real.

### Compressed schedule reminder

10 working days total, 60 hours. GUI is mandatory not optional. Hard rule:
backend on the §2 critical path comes first; if backend slips on a given
day, that day's GUI is dropped, not vice versa.
