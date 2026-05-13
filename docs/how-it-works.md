# How it works — Cisco AI Config Agent, plain-English technical overview

A reference for anyone who wants to understand what this project does and
how each piece fits together — without reading every file. Mirrors what's
actually shipped as of end-of-day 2026-05-12.

---

## 1. What the project does

You type something in plain language (Slovak or English):

> *"ukáž mi rozhrania"* — "show me the interfaces"
> *"zmeň hostname na LAB-R4 v prehliadači"* — "change hostname to LAB-R4 in the browser"

An AI brain (Claude) decides which tool to use, what parameters to pass,
and — for anything that changes the router — asks you to click **Approve**
in a web UI first. Only then does the tool actually touch the router.

There are **two ways** the agent can change the router:

1. **CLI path** — fast. The agent opens an SSH connection and types
   IOS commands like `configure terminal` → `hostname LAB-R4` → `end`.
2. **WebUI path** — slower but visual. The agent launches a real Chrome
   browser, logs into the router's web interface, navigates to the right
   form, fills the field, and clicks the blue **Apply** button. You can
   watch it happen.

Every change leaves an evidence trail on disk: SSH transcripts, screenshots
before and after, and a snapshot of `show running-config` from before and
after the change.

---

## 2. The big picture (one diagram)

```
┌──────────────────────────────────────────────────────────────┐
│  Browser (Next.js GUI)                                       │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │  Chat   │  │Preview   │  │Dashboard │  │WebUI Live   │   │
│  │  page   │  │(Approve) │  │(activity)│  │(screenshots)│   │
│  └────┬────┘  └────┬─────┘  └─────┬────┘  └──────┬──────┘   │
│       │            │              │              │           │
└───────┼────────────┼──────────────┼──────────────┼───────────┘
        │ POST       │ POST         │ GET          │ GET
        │ /api/chat  │ /api/approve │ /api/logs    │ /ws/agent (WS)
        ▼            ▼              ▼              ▼
┌──────────────────────────────────────────────────────────────┐
│  Backend (FastAPI)                                           │
│                                                              │
│  POST /api/chat ──► run_planner() ──► Claude Haiku 4.5      │
│                          │           "what tool should I    │
│                          │            call?"                 │
│                          ▼                                   │
│                    tool_registry.execute_tool()              │
│                          │                                   │
│           ┌──────────────┼──────────────────────┐           │
│           ▼              ▼                      ▼            │
│      read_tools     write_tools (CLI)       webui_agent     │
│      (SSH)          (SSH, HITL-gated)       (Playwright,    │
│                                              HITL-gated)    │
│           │              │                      │            │
└───────────┼──────────────┼──────────────────────┼───────────┘
            │              │                      │
            ▼              ▼                      ▼
       ┌─────────────────────┐         ┌──────────────────┐
       │  Cisco C1111 router │         │ Chromium browser │
       │  192.168.10.1       │◄────────┤ driven by        │
       │  - SSH port 22      │         │ Playwright       │
       │  - HTTPS port 443   │◄────────┴──────────────────┘
       └─────────────────────┘
```

---

## 3. The tools (each: what it IS in one line, what we use it for)

| Tool | What it is | What we use it for |
|---|---|---|
| **Python 3.13** | The language | Everything backend |
| **FastAPI** | A Python library that builds web APIs | The backend at `localhost:8000` with `/api/chat`, `/api/approve`, etc. |
| **Anthropic SDK** | The official Python client for Claude | Send the user's message to Claude and get back which tool to call |
| **Claude Haiku 4.5** | The AI model that picks tools | Reads natural language, picks the right tool, extracts parameters |
| **Netmiko** | A Python library for SSH'ing into network devices | Sends Cisco CLI commands like `hostname LAB-R4` and reads `show` output |
| **ntc-templates** | A library of TextFSM parsers for Cisco CLI output | Turns raw `show ip interface brief` text into a clean Python list of dicts |
| **Playwright** | A library that drives a real Chrome browser from Python | Logs into the router's web UI and clicks the buttons |
| **Chromium** | The actual browser binary Playwright launches | What you see open on your screen during the WebUI demo |
| **Pydantic Settings** | A library that loads typed config from `.env` | Loads `ROUTER_HOST`, `ROUTER_SSH_USER`, etc. with type safety |
| **structlog** | A library that produces structured JSON log lines | Writes one JSON line per tool call to `logs/actions.log` |
| **Next.js 14 + Tailwind** | The frontend framework + CSS | The web GUI you see at `localhost:3000` |
| **ChromaDB** | A local vector database | Stores Cisco doc chunks for RAG retrieval (Day 6: shipped; 692 chunks from ISR1100 SW Config Guide) |

---

## 4. The HITL (human-in-the-loop) approval gate — why writes need a click

> **Hard rule from `PROJECT_PLAN.md §4.4`:** the agent CANNOT change the
> router without a human clicking Approve. This is enforced server-side,
> not by the AI prompt.

Every write request follows the same lifecycle:

```
                              user clicks
PROPOSED ──► APPROVED ──────► EXECUTED ──► VERIFIED
   │                             │
   │                             ▼
   ▼                          FAILED
REJECTED
```

The "thing" that moves through these states is called an **action_id** —
a string like `act_20260512_441f6c`. The state machine lives in
`backend/orchestration/confirmations.py`.

**Two layers of defense:**

1. The orchestrator dispatcher checks `is_approved(action_id)` before
   calling the write function. No approval → returns `"not_approved"`
   error, function is never called.
2. The write function itself ALSO checks `is_approved(action_id)`. So
   even if a future bug bypasses layer 1, layer 2 stops the write.

---

## 5. Scenario A — hostname change via CLI (the fast path)

### What the user does

In the chat: `zmeň hostname na LAB-R1`

### What happens, step by step

| # | Who | What |
|---|---|---|
| 1 | Browser | `POST /api/chat` with the message |
| 2 | Planner | Claude Haiku 4.5 sees the message + tool catalog, picks `propose_set_hostname(new_name="LAB-R1")` |
| 3 | Dispatcher | Calls `_propose_set_hostname("LAB-R1")` |
| 4 | Confirmations store | Creates action_id `act_xxx` in state `PROPOSED` |
| 5 | Planner | Returns to Claude: `{action_id: "act_xxx", execute_tool: "set_hostname", …}` |
| 6 | Claude | Composes a Slovak response: "Návrh pripravený. Action ID: act_xxx. Otvor /preview..." |
| 7 | Browser | User opens `http://localhost:3000/preview?action_id=act_xxx` |
| 8 | ApprovalButtons.tsx | User clicks **APPROVE** button → `POST /api/approve/act_xxx` |
| 9 | Confirmations | State changes `PROPOSED` → `APPROVED` |
| 10 | Browser | User goes back to chat: "akcia act_xxx je schválená, vykonaj ju" |
| 11 | Planner | Claude sees the action_id reference, calls `set_hostname(new_name="LAB-R1", action_id="act_xxx")` |
| 12 | Dispatcher | Checks `is_approved("act_xxx")` → True, calls the function |
| 13 | `set_hostname` | Pre-snapshot via SSH (3 show commands → 3 .txt files) |
| 14 | `set_hostname` | Netmiko opens SSH, sends `configure terminal` → `hostname LAB-R1` → `end` |
| 15 | `set_hostname` | Invalidates SSH pool (the router's prompt just changed) |
| 16 | `set_hostname` | Post-snapshot via SSH (3 more show commands → 3 .txt files) |
| 17 | Confirmations | State `APPROVED` → `EXECUTED` |
| 18 | structlog | Writes `tool_call` JSONL line to `logs/actions.log` |
| 19 | Browser | Claude returns "Hotovo! Hostname LAB-R1 set in 1.29 s" |

**The actual CLI commands** Netmiko sends in step 14:

```
configure terminal
hostname LAB-R1
end
```

**Real proof:** the Day 4 smoke ran this exact flow in **1.29 seconds**.

---

## 6. Scenario B — hostname change via WebUI (the visual path)

### What the user does

In the chat: `zmeň hostname na LAB-R4 v prehliadači`

The phrase `v prehliadači` (or `via WebUI`, `demo`, `ukáž mi ako`) tells
Claude to pick the WebUI tool instead of the CLI tool.

### What happens, step by step

Steps 1–10 are the same as Scenario A but with `propose_webui_set_hostname`
and `webui_set_hostname` instead. The interesting part is step 13+:

| # | Who | What |
|---|---|---|
| 13 | `change_hostname_via_webui` | Pre-snapshot via SSH (3 files) |
| 14 | `webui_browser` | Launches **headed Chromium**, viewport 1400x900, `ignore_https_errors=True` for the router's self-signed cert |
| 15 | `evidence.step()` | Screenshot 01-browser-launched.png |
| 16 | `login()` | `page.goto("https://192.168.10.1")`, fills username + password, clicks Log In |
| 17 | `evidence.step()` | Screenshot 02-logged-in.png |
| 18 | `HostnamePage.goto()` | `page.goto("https://192.168.10.1/webui/#/general")` — **skips the sidebar** by hitting the hash route directly |
| 19 | `evidence.step()` | Screenshot 03-hostname-form.png |
| 20 | `HostnamePage.get_current_hostname()` | Reads value from `<input name="switchName">` |
| 21 | `HostnamePage.set_hostname()` | Clicks the input (focus), then `.fill("LAB-R4")` (clears + types) |
| 22 | AngularJS | `ng-change` fires → enables the Apply button |
| 23 | `evidence.step()` | Screenshot 04-form-filled.png |
| 24 | `HostnamePage.apply()` | Clicks the blue **Apply** button — specifically `<button kendo-button="saveBtn" ng-click="apply('General')">` |
| 25 | `evidence.step()` | Screenshot 05-applied.png |
| 26 | `webui_browser` | Closes Chromium |
| 27 | `change_hostname_via_webui` | Invalidates SSH pool (the router's prompt just changed) |
| 28 | `verify_hostname("LAB-R4")` | Opens fresh SSH, runs `show running-config`, regex-matches `^\s*hostname LAB-R4\s*$` |
| 29 | `change_hostname_via_webui` | Post-snapshot via SSH (3 files) |
| 30 | Browser | Claude returns "Hotovo! Hostname changed to LAB-R4" |

**The exact buttons/fields the agent clicks/types into:**

| Element | What the user sees | What's in the DOM |
|---|---|---|
| Username field | "Username" textbox | `<input>` matched by `get_by_label("Username", exact=False)` |
| Password field | "Password" textbox | `<input>` matched by `get_by_label("Password", exact=False)` |
| Login button | "Log In" button | matched by `get_by_role("button", name="Log In")` |
| Host Name field | "Host Name*" textbox showing current name | `<input name="switchName" id="switchName" data-ng-model="jsonData.general.name">` |
| Apply button | Blue "Apply" button with floppy-disk icon | `<button kendo-button="saveBtn" class="k-button btn btn-primary" ng-click="apply('General')">` |

**Real proof:** end-of-day 2026-05-12, the full WebUI hostname change ran
in **23 seconds** against the actual Cisco C1111. Logs at the bottom of
this doc.

---

## 7. The actual code — annotated

### 7.1 The orchestrator's tool-use loop (`backend/orchestration/planner.py`)

```python
def run_planner(user_message, history=None, client=None):
    client = client or Anthropic(api_key=settings.anthropic_api_key)
    messages = list(history or [])
    messages.append({"role": "user", "content": user_message})

    for iteration in range(MAX_ITERATIONS):     # cap at 8 tool calls per turn
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",   # fast + cheap
            system=SYSTEM_PROMPT,                # SK/EN rules + tool catalog
            tools=TOOL_SCHEMAS,                  # the 10 tools we expose
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return PlannerResult(final_text=..., events=...)

        # Claude wants to call one or more tools
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input)   # ← our dispatcher
                tool_results.append({"type": "tool_result",
                                     "tool_use_id": block.id,
                                     "content": json.dumps(result)})

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
        # loop continues — Claude sees the tool results and decides next step
```

### 7.2 The WebUI hostname flow (`backend/webui_agent/flows/change_hostname.py`)

```python
def change_hostname_via_webui(new_name, action_id, *, headless=False):
    _guard(action_id)            # HITL: refuse without APPROVED state

    ev = EvidenceCollector("change_hostname", action_id=action_id)
    pre_dir = take_snapshot(action_id, "pre")     # SSH grabs running-config etc.

    with webui_browser(headless=headless) as page:
        ev.step("01-browser-launched", page)

        login(page)                # multi-strategy fallback for the Cisco login form
        ev.step("02-logged-in", page)

        hp = HostnamePage(page)
        hp.goto()                  # page.goto("/webui/#/general") — skips the sidebar
        ev.step("03-hostname-form", page)

        old = hp.get_current_hostname()
        hp.set_hostname(new_name)  # click() + fill() into <input name="switchName">
        ev.step("04-form-filled", page)

        hp.apply()                 # click() the kendo-button[ng-click="apply('General')"]
        ev.step("05-applied", page)

    # Router's prompt just changed — drop the stale SSH session
    pool.invalidate(settings.router_host, settings.router_ssh_user)

    # CLI is the ground truth — confirm the change really took
    if not verify_hostname(new_name):
        raise WebUIVerificationError(...)

    post_dir = take_snapshot(action_id, "post")
    mark_executed(action_id)
    return {"tool": "webui_set_hostname", "old": old, "new": new_name,
            "snapshot_pre": str(pre_dir), "snapshot_post": str(post_dir),
            "screenshots": str(ev.session_dir), "verified": True}
```

### 7.3 The dispatcher with two-layer approval gate (`backend/orchestration/tool_registry.py`)

```python
_REQUIRES_APPROVAL = frozenset({"set_hostname", "set_interface_ip",
                                "webui_set_hostname"})

def execute_tool(name, params):
    if name not in _TOOL_FUNCS:
        return {"error": f"unknown tool: {name!r}"}

    # Layer 1: dispatcher refuses write tools whose action_id isn't APPROVED
    if name in _REQUIRES_APPROVAL:
        action_id = params.get("action_id")
        if not action_id or not is_approved(action_id):
            return {"error": "not_approved", ...}

    func = _TOOL_FUNCS[name]
    try:
        result = func(**params)        # ← actual call into read/write/webui code
    except NotApproved as exc:
        # Layer 2: write tool itself raises NotApproved if approval revoked
        # between dispatcher check and function entry (race)
        return {"error": "not_approved", "message": str(exc)}
    except Exception as exc:
        return {"error": "tool_failed", "exc_type": type(exc).__name__,
                "message": str(exc) or repr(exc)}

    return result if isinstance(result, dict) else {"result": result}
```

---

## 8. What works today (end of Day 5, 2026-05-12)

All six §2 demo scenarios from `PROJECT_PLAN.md`:

| # | Scenario | Tool | Status |
|---|---|---|---|
| 1 | CLI: show interfaces | `show_ip_interface_brief` | ✓ proven on real router |
| 2 | CLI: show running-config | `show_running_config` | ✓ proven |
| 3 | CLI: change hostname | `set_hostname` | ✓ proven (1.29 s end-to-end) |
| 4 | CLI: change interface IP | `set_interface_ip` | built + unit-tested (live smoke deferred to avoid breaking `Gi0/1/0` management) |
| 5 | **WebUI: change hostname** | `webui_set_hostname` | ✓ **proven on real router today, 23 s end-to-end** |
| 6 | **WebUI: add VLAN** | `webui_add_access_vlan` | ✓ shipped Day 7; POM + flow + 26 unit tests; smoke harness runnable |
| 7 | **RAG: query Cisco docs with citations** | `search_docs` | ✓ shipped Day 6; 772 chunks (2 of 7 PDFs); chat shows Sources badges |

**Plus everything around it:**

- HITL approval gate (server-enforced, two layers)
- Pre/post device snapshots on every write
- Screenshot evidence on every WebUI step
- structlog JSONL log of every tool call (visible live in the Dashboard)
- Real-time Slovak chat with Claude Haiku 4.5
- The 4 GUI pages all wired to real data (Dashboard, Preview, /chat,
  WebUI Live — /chat does sync POST for the reply + live WS event
  stream from `/ws/agent` for the planner's tool_call / applied /
  awaiting_approval activity)

**Test coverage:** 183 tests passing (157 baseline + 26 from Day 7:
VlanPage POM, add_access_vlan flow, VLAN tool registry), ruff clean.
Plus a smoke harness at `scripts/run_smoke_tests.py` that runs the
6 §2 scenarios with explicit `SMOKE_ALLOW_WRITES` gate.

---

## 9. Proof: actual end-to-end run log from today (2026-05-12 12:26)

This is the real terminal output from running the WebUI hostname change
against the cabled Cisco C1111 — copy/paste, no edits:

```
>>> aid = propose_action("webui_set_hostname", {"name": "LAB-R4"})
>>> approve_action(aid)
{'action_id': 'act_20260512_441f6c', 'tool': 'webui_set_hostname',
 'params': {'name': 'LAB-R4'}, 'state': <ActionState.APPROVED: 'APPROVED'>,
 'created_at': '2026-05-12T10:26:08.935474+00:00', ...}
>>> result = change_hostname_via_webui("LAB-R4", action_id=aid, headless=False)
12:26:20 [info ] change_hostname_via_webui_start new_name=LAB-R4
12:26:20 [info ] evidence_session_started  path=...change_hostname_act_20260512_441f6c
12:26:21 [info ] connection_created        host=192.168.10.1 user=cisco
12:26:22 [info ] snapshot_taken            phase=pre
12:26:22 [info ] webui_browser_launched
12:26:23 [info ] evidence_step             label=01-browser-launched
12:26:23 [info ] webui_login_attempt       base_url=https://192.168.10.1 user=cisco
12:26:25 [info ] webui_login_complete      url=https://192.168.10.1/webui/
12:26:26 [info ] evidence_step             label=02-logged-in
12:26:26 [info ] hostname_page_direct_nav  target=https://192.168.10.1/webui/#/general
12:26:39 [info ] hostname_page_goto_complete
12:26:39 [info ] evidence_step             label=03-hostname-form
12:26:39 [info ] hostname_page_read        current=LAB-R3
12:26:40 [info ] hostname_page_filled      new_name=LAB-R4
12:26:40 [info ] evidence_step             label=04-form-filled
12:26:41 [info ] hostname_page_apply_clicked
12:26:41 [info ] evidence_step             label=05-applied
12:26:41 [info ] webui_browser_closed
12:26:41 [info ] connection_invalidated    host=192.168.10.1 user=cisco
12:26:42 [info ] connection_created        host=192.168.10.1 user=cisco
12:26:42 [info ] tool_call duration_ms=1204 result_summary='6686 chars'
                tool=show_running_config
12:26:42 [info ] verify_hostname           expected=LAB-R4 found=True
12:26:43 [info ] snapshot_taken            phase=post
12:26:43 [info ] change_hostname_via_webui_complete new=LAB-R4 old=LAB-R3
```

**23 seconds.** Pre + post snapshots saved. 5 screenshots saved. CLI
verification confirmed the change. Action marked EXECUTED.

Artifacts on disk:
```
artifacts/device-snapshots/act_20260512_441f6c/
├── pre/
│   ├── running-config.txt
│   ├── version.txt
│   └── ip-int-brief.txt
└── post/
    ├── running-config.txt
    ├── version.txt
    └── ip-int-brief.txt

artifacts/screenshots/change_hostname_act_20260512_441f6c/
├── 01-01-browser-launched.png
├── 02-02-logged-in.png
├── 03-03-hostname-form.png
├── 04-04-form-filled.png
└── 05-05-applied.png
```

---

## 10. Where to look in the code

| Want to understand… | Look at |
|---|---|
| How natural language becomes tool calls | `backend/orchestration/planner.py` |
| The tool catalog Claude sees | `backend/orchestration/tool_registry.py` (`TOOL_SCHEMAS`) |
| The HITL state machine | `backend/orchestration/confirmations.py` |
| How `show` commands work | `backend/cli_agent/read_tools.py`, `parsers.py` |
| How CLI writes work | `backend/cli_agent/write_tools.py`, `snapshots.py` |
| The Playwright browser config | `backend/webui_agent/browser.py` |
| The login flow | `backend/webui_agent/login.py` |
| The hostname form clicks | `backend/webui_agent/pages/hostname_page.py` |
| The full WebUI hostname flow | `backend/webui_agent/flows/change_hostname.py` |
| Selector strategies (role/label/text/css) | `backend/webui_agent/selectors/iosxe_default.yaml` |
| Screenshot + DOM dump helper | `backend/webui_agent/evidence.py` |
| The frontend Preview screen | `frontend/components/preview/ApprovalButtons.tsx` |
| The Live Agent screen | `frontend/components/webui-agent/{PhaseProgress,ActionTimeline}.tsx` |
| The full plan | `PROJECT_PLAN.md` (§7 day-by-day) |
| Per-day shipped reports | `docs/day1-summary.md`, `docs/day2-5-summary.md` |
