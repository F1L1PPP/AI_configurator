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
pytest -q
```

## Project structure

See `PROJECT_PLAN.md §5` for the full annotated layout.

## Configuration

All settings are loaded from `.env` via Pydantic Settings (`backend/core/settings.py`).
See `.env.example` for every required key.
