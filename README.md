# Cisco AI Config Agent

AI-powered network configuration for Cisco routers. You type plain language ("change hostname to LAB-R1", "add VLAN 30 named OFFICE", "configure OSPF on the management subnet"), Claude drafts a plan, you click **Approve** + **Execute Now**, and Python actually touches the device over SSH or drives the WebUI through Playwright. Every write goes through a server-enforced human-in-the-loop gate — the model can't bypass it. Every action is snapshotted before and after, and a universal conflict detector warns you when a propose would overwrite something already in `running-config` (or `vlan.dat`, on the C1111-4P).

CLI agent over SSH (Netmiko), WebUI agent over Playwright (subprocess-isolated for reliability), RAG grounding from the curated Cisco IOS XE WebUI guide, structured evidence on disk for every action, friendly degradation when Anthropic's API is overloaded (auto-retry + clear error message).

## Screenshots

### Dashboard — real-time router health

KPIs, recent activity feed, and the live Device Overview card (hostname, IP, IOS version, uptime, last-backup timestamp) are all wired to the real router via `GET /api/devices` + `GET /api/devices/{id}/last-backup`. No mocked data.

<!-- PASTE image1.png HERE → save as docs/screenshots/dashboard.png -->
<img width="1918" height="1197" alt="Dashboard" src="https://github.com/user-attachments/assets/568304a5-5426-4f45-9b5a-e275f89b3932" />


### AI Configuration — chat with your router

Plain-English requests get drafted into IOS XE commands or WebUI click plans by Claude Haiku 4.5 (grounded by RAG over the Cisco docs). The suggestion chips below the input are context-aware — they reflect what's actually configured on your router right now, so they don't suggest "create VLAN 30" if VLAN 30 already exists. Live event stream on the right shows every tool call, every config line being typed, and every snapshot taken.

<!-- PASTE image4.png HERE → save as docs/screenshots/ai-configuration.png -->
<img width="1908" height="1192" alt="AI-configuration" src="https://github.com/user-attachments/assets/fb65e75c-c273-4f04-8351-8f5b07750496" />


### Config Preview — real before/after diff

Every executed action saves a `running-config` snapshot to disk before and after. The Config Preview page reads both and renders a line-numbered diff with added lines highlighted `+`. No more "did my change actually land?" — you can see the byte-count delta, the timestamp change, and every config line that was rewritten.

<!-- PASTE image3.png HERE → save as docs/screenshots/config-preview.png -->
<img width="1908" height="1197" alt="Config-preview" src="https://github.com/user-attachments/assets/2a94511c-f3d8-4977-ac19-44d06803d0f4" />


### Devices — connection management

Add a new router by IP + credentials. The agent connects over SSH (HTTPS WebUI is bootstrapped on first approved write), runs `show version`, and surfaces real model / IOS / uptime in the Connected Devices table. The single-device path is the current focus; multi-device discovery is on the roadmap.

<!-- PASTE image2.png HERE → save as docs/screenshots/devices.png -->
<img width="1918" height="1197" alt="Devices" src="https://github.com/user-attachments/assets/d99d4239-b56d-4376-8d51-dd85a093bc52" />


### Settings — safety contract is visible

Appearance toggles (light/dark, accent color), agent endpoint URLs, and the three load-bearing safety switches:
- **Require APPROVE before EXECUTE** — the two-click contract, cannot be disabled.
- **Auto-snapshot before write** — saves `running-config` to `artifacts/device-snapshots/<action_id>/pre/` so the diff always has a baseline.
- **Open Chromium during WebUI flows** — headed-mode so you can watch the agent click; turn off for headless CI runs.

<!-- PASTE image5.png HERE → save as docs/screenshots/settings.png -->
<img width="1918" height="1197" alt="Settings" src="https://github.com/user-attachments/assets/d43fff2c-1a43-42cf-b8ae-c69f2139b16c" />

## Stack

- **Backend**: Python 3.12 · FastAPI · Pydantic Settings · structlog · Netmiko · Playwright (subprocess-isolated) · ChromaDB · sentence-transformers/all-MiniLM-L6-v2 · Anthropic SDK
- **Frontend**: plain React via CDN + Babel-in-browser (no bundler, no npm). Served same-origin by FastAPI's `StaticFiles` mount at `/`.
- **Production LLM**: Claude Haiku 4.5 for both outer and inner planners.
- **Target device**: Cisco C1111-4P running IOS XE 17.6.3a.

## Prerequisites

- **Windows 10/11 64-bit** (the project is Windows-first because of the Playwright subprocess-isolation pattern in `backend/webui_agent/`; Mac/Linux untested)
- **Python 3.12** — `winget install Python.Python.3.12` or [python.org](https://www.python.org/downloads/)
- **Git** — `winget install Git.Git`
- **Anthropic API key** — sign up at [console.anthropic.com](https://console.anthropic.com/), generate a key, keep it handy for the `.env` step below
- **A Cisco router or virtual lab** reachable on your LAN over SSH. Tested on Cisco C1111-4P running IOS XE 17.6.3a. Other IOS XE devices should work but the WebUI flows are tuned to the C1111 menu layout.

No Node.js, no npm — the frontend ships as static `.jsx` files transpiled in-browser by Babel CDN. Uvicorn serves them at `/`.

## Install

1. **Clone and enter the repo**
   ```powershell
   git clone https://github.com/F1L1PPP/AI_configurator.git
   cd AI_configurator
   ```

2. **Create the Python virtualenv and install dependencies**
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   First install pulls ~600 MB (Playwright + torch + ChromaDB embeddings). One-time cost; subsequent installs are fast.

3. **Install the git pre-commit hook** (auto-runs `ruff check` + `ruff format` before each commit)
   ```powershell
   pre-commit install
   ```

4. **Install Playwright browsers** (needed for the WebUI agent flows)
   ```powershell
   playwright install chromium
   ```

## Configure

1. **Copy the example env file**
   ```powershell
   copy .env.example .env
   ```

2. **Open `.env` in any editor and fill these required fields:**
   ```ini
   # Anthropic
   ANTHROPIC_API_KEY=sk-ant-...

   # Router
   ROUTER_HOST=192.168.10.1          # your router's management IP
   ROUTER_SSH_USER=cisco             # SSH username
   ROUTER_SSH_PASSWORD=...           # SSH password
   ROUTER_WEBUI_USER=admin           # WebUI HTTPS username
   ROUTER_WEBUI_PASSWORD=...         # WebUI HTTPS password
   ```
   See `.env.example` for the optional fields (log level, allowed origins, etc.).

3. **Validate your `.env`** — any missing or wrong-typed field surfaces here as a clear error:
   ```powershell
   python -c "from backend.core.settings import get_settings; s = get_settings(); print('OK — router_host=', s.router_host)"
   ```

4. **Seed the SSH known_hosts** (Netmiko refuses unknown SSH hosts; do this once per router):
   ```powershell
   ssh -o StrictHostKeyChecking=accept-new <YOUR_ROUTER_HOST>
   ```
   The lab router's login prompt appears — hit `Ctrl-C`, the host key is now saved.

## Run

```powershell
.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --port 8000
```

You'll see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

Open **http://localhost:8000/** — uvicorn serves the API (`/api/*`, `/ws/agent`) and the frontend SPA on the same origin.

First page load fetches device state from your router via `show version` — if you see your router's hostname in the sidebar's **ACTIVE DEVICE** card, you're connected.

Then type a request in the chat: `change hostname to LAB-R1`, `add VLAN 30 named OFFICE`, or `show me what's currently configured`. Approve → Execute when the proposal looks right.

## How it works (one paragraph)

Your message goes to `POST /api/chat`. An outer Claude Haiku 4.5 tool-use loop picks a tool from the registry (read tools run immediately; write tools route through `propose_cli_configure(intent)` or `propose_webui_configure(intent, webui_path)`). The inner Haiku planner — grounded by RAG and (for WebUI) a JSON snapshot of the live page (`describe_page`) — drafts the actual command list or click plan, attached to a server-side `action_id`. You click **Approve** → `POST /api/approve/{id}`, then **Execute Now** → `POST /api/execute/{id}`. The execute route atomically pre-transitions APPROVED → EXECUTING (TOCTOU-safe) and only THEN dispatches the deterministic Python code that touches the device. Every action emits live events on `/ws/agent` for the UI's live stream column. A universal conflict detector (`backend/orchestration/conflict_detector.py`) checks the propose against the router's current `running-config` (and `show vlan brief` for VLAN-shaped writes on devices where VLANs live in `vlan.dat`) so the UI can render a **REPLACES EXISTING** or **IDENTICAL CONFIG** block above the commands before you approve. See [`docs/how-it-works.md`](docs/how-it-works.md) for the full walkthrough.

## Lint & tests

```powershell
.venv\Scripts\Activate.ps1
ruff check .
mypy                 # gated in CI — pyproject.toml [tool.mypy]
pytest -q
```

588 unit tests; a handful are gated behind `SMOKE_ALLOW_WRITES=1` because they actually mutate the lab router.

## Troubleshooting

**`uvicorn: command not found` / `Will watch for changes...` then `Error loading ASGI app`**
Activate the venv first: `.venv\Scripts\Activate.ps1`. The shell prompt should show `(.venv)`. Then `uvicorn backend.main:app --reload --port 8000`.

**`ANTHROPIC_API_KEY` ValidationError on startup**
Your `.env` is missing the key, OR your shell has an empty `ANTHROPIC_API_KEY=` exported in `$PROFILE` that shadows it. Run `$env:ANTHROPIC_API_KEY` to check; unset with `Remove-Item Env:ANTHROPIC_API_KEY`.

**Chat shows "Claude API is temporarily overloaded (HTTP 529)..."**
Anthropic is rate-limiting. The SDK already retried 5 times. Wait a minute and resend the message — the agent state is preserved.

**Proposal shows "IDENTICAL CONFIG — APPLYING WILL BE A NO-OP"**
That's a feature, not an error. Your request asked for a state the router already has (e.g. "change hostname to LAB-R1" when it's already LAB-R1). Approve to confirm the redundant write, or Reject to cancel.

**SSH timeouts mid-session**
Cisco SSH idle-times out after ~10 min by default. The agent reconnects automatically on the next read; first call after idle takes ~1.5 s instead of ~400 ms. Not a bug.

**`playwright install chromium` fails**
Run from an elevated PowerShell (right-click → Run as Administrator). Playwright needs admin to register Chromium.

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
