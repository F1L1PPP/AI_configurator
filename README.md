# Cisco AI Config Agent

AI-assisted configuration agent for Cisco C1111 — CLI read/write, WebUI automation,
RAG-grounded knowledge, and a human-in-the-loop approval gate.

## Prerequisites

- Windows 10/11 64-bit
- Python 3.12 (`winget install Python.Python.3.12` or python.org)
- Node.js 20 LTS (`winget install OpenJS.NodeJS.LTS`)
- Git

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

# Frontend deps
cd frontend
npm install
cd ..

# First-time SSH host-key acceptance for the lab router. Netmiko refuses
# unknown hosts; this seeds known_hosts so the backend can connect.
ssh -o StrictHostKeyChecking=accept-new <ROUTER_HOST_FROM_DOTENV>
# (You'll see the lab router login prompt — type Ctrl-C, the host key is
#  already saved.)
```

## Run (development)

```powershell
# Backend
.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload

# Frontend (separate terminal)
cd frontend
npm run dev
```

## Lint & tests

```powershell
.venv\Scripts\Activate.ps1
ruff check .
mypy                 # gated in CI — pyproject.toml [tool.mypy]
pytest -q
pytest -m "not webui" -q   # fast iteration; skips WebUI-agent layer
```

## Debug / Operations helpers

A few one-shot CLI tools live in `tools/` for poking at the running system.

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

See `PROJECT_PLAN.md §5` for the full annotated layout.

## Configuration

All settings are loaded from `.env` via Pydantic Settings (`backend/core/settings.py`).
See `.env.example` for every required key.

### Validate your `.env`

After editing `.env`, sanity-check it by loading the settings model. Any
missing required key or wrong type surfaces here as a clear error:

```powershell
python -c "from backend.core.settings import get_settings; s = get_settings(); print('OK — router_host=', s.router_host, '| log_level=', s.log_level)"
```

If you see `pydantic_core._pydantic_core.ValidationError`, fix the field
the error names and re-run.
