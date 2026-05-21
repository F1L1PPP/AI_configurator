# Cisco AI Config Agent

AI-powered network configuration for Cisco routers. You type plain language ("change hostname to LAB-R1", "add VLAN 30 named OFFICE"), Claude drafts a plan, you click **Approve** + **Execute**, and Python actually touches the device. Every write goes through a server-enforced human-in-the-loop gate — the model can't bypass it.

CLI agent over SSH (Netmiko), WebUI agent over Playwright, RAG grounding from the Cisco IOS XE WebUI guide, structured evidence on disk for every action.

## Screenshots

| Dashboard | AI Configuration | Devices |
|---|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![AI Configuration](docs/screenshots/ai-configuration.png) | ![Devices](docs/screenshots/devices.png) |

## Stack

- **Backend**: Python 3.12 · FastAPI · Pydantic Settings · structlog · Netmiko · Playwright (subprocess-isolated) · ChromaDB · sentence-transformers/all-MiniLM-L6-v2 · Anthropic SDK
- **Frontend**: plain React via CDN + Babel-in-browser (no bundler, no npm). Served same-origin by FastAPI's `StaticFiles` mount at `/`.
- **Production LLM**: Claude Haiku 4.5 for both outer and inner planners.
- **Target device**: Cisco C1111-4P running IOS XE 17.6.3a.

## Prerequisites

- Windows 10/11 64-bit
- Python 3.12 (`winget install Python.Python.3.12` or python.org)
- Git

No Node.js, no npm — the new frontend ships as static files served by uvicorn.

## Install

```powershell
git clone https://github.com/F1L1PPP/AI_configurator.git
cd AI_configurator

# Python virtualenv
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Install git hooks (runs ruff before every commit)
pre-commit install

# Copy and fill secrets
copy .env.example .env
# edit .env — add ANTHROPIC_API_KEY, router credentials, etc.

# First-time SSH host-key acceptance for the lab router. Netmiko refuses
# unknown hosts; this seeds known_hosts so the backend can connect.
ssh -o StrictHostKeyChecking=accept-new <ROUTER_HOST_FROM_DOTENV>
# (You'll see the lab router login prompt — type Ctrl-C, the host key is
#  already saved.)
```

## Run

```powershell
.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --port 8000
```

Then open **http://localhost:8000/** — uvicorn serves both the API (`/api/*`, `/ws/agent`) and the SPA (`/`) on the same origin.

## How it works (one paragraph)

Your message goes to `POST /api/chat`. An outer Claude Haiku 4.5 tool-use loop picks a tool from the registry (read tools run immediately; write tools route through `propose_cli_configure(intent)` or `propose_webui_configure(intent, webui_path)`). The inner Haiku planner — grounded by RAG and (for WebUI) a JSON snapshot of the live page (`describe_page`) — drafts the actual command list or click plan, attached to a server-side `action_id`. You click **Approve** → `POST /api/approve/{id}`, then **Execute** → `POST /api/execute/{id}`. The execute route atomically pre-transitions APPROVED → EXECUTING (TOCTOU-safe) and only THEN dispatches the deterministic Python code that touches the device. Every action emits live events on `/ws/agent` for the UI's live stream column. See [`docs/how-it-works.md`](docs/how-it-works.md) for the full walkthrough.

## Lint & tests

```powershell
.venv\Scripts\Activate.ps1
ruff check .
mypy                 # gated in CI — pyproject.toml [tool.mypy]
pytest -q
```

523 tests; 3 are gated behind `SMOKE_ALLOW_WRITES=1` because they actually mutate the lab router.

## Debug / operations helpers

```powershell
# Show ChromaDB collection size + sample chunks
python tools/check_vectorstore.py

# Interactive RAG search ("does the agent find the right doc for X?")
python tools/query_rag.py "how do I configure OSPF"
```

End-of-day rollup (lint + test + commit + push + annotated backup tag):

```powershell
scripts\checkpoint.ps1
```

## Project structure

See [`PROJECT_PLAN.md §5`](PROJECT_PLAN.md) for the full annotated layout.

## Configuration

All settings load from `.env` via Pydantic Settings ([`backend/core/settings.py`](backend/core/settings.py)). See `.env.example` for every required key.

### Validate your `.env`

After editing `.env`, sanity-check it by loading the settings model. Any missing required key or wrong type surfaces here as a clear error:

```powershell
python -c "from backend.core.settings import get_settings; s = get_settings(); print('OK — router_host=', s.router_host, '| log_level=', s.log_level)"
```

If you see `pydantic_core._pydantic_core.ValidationError`, fix the field the error names and re-run.

## Docs

- [`docs/how-it-works.md`](docs/how-it-works.md) — plain-English architecture walkthrough
- [`docs/plan-ai-first-webui.md`](docs/plan-ai-first-webui.md) — the AI-first WebUI execution model (Phases 0–5 shipped through `v0.4.0-alpha.4-settle-wait`)
- [`docs/smoke-scenarios.md`](docs/smoke-scenarios.md) — the six baseline scenarios that gate alpha-1
- [`docs/today-2026-05-18-evening-summary.md`](docs/today-2026-05-18-evening-summary.md) — most recent session wrap
- [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — full project plan and decisions
- [`CLAUDE.md`](CLAUDE.md) — rules for the coding agent
