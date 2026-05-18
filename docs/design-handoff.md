# Design handoff — Cisco AI Config Agent

A working brief for the designer redesigning the UI. Describes where the current code lives, what the user sees today, and the key interaction patterns that need to survive any redesign because they're load-bearing for safety.

---

## Where the code lives

- **Repo**: https://github.com/F1L1PPP/AI_configurator
- **Branch**: `feature/bootstrap` (the only active branch; `main` is behind)
- **Frontend folder**: `frontend/` at the repo root
- **Stack**: Next.js 14 (App Router), TypeScript, Tailwind CSS. No CSS modules, no styled-components — utility classes only.
- **Run locally**: `cd frontend && npm install && npm run dev` (port 3000). Backend must be running separately on port 8000.

The frontend is the thin layer; all routing/reasoning happens server-side. The UI's job is to render chat, capture clicks (especially the safety-critical APPROVE/EXECUTE pair), and stream live events.

---

## Current page map (`frontend/app/`)

| Route | File | What it is |
|---|---|---|
| `/` | `app/page.tsx` | Landing / dashboard. Shows backend status, action count, recent actions. Mostly an entry point. |
| `/chat` | `app/chat/page.tsx` | **The primary surface.** All AI-driven configuration happens here. Chat with the agent, approve/execute writes, watch live event stream. |
| `/actions` | `app/actions/page.tsx` | Lists pre-built "scenario" forms — hand-coded fast paths for hostname change, VLAN add, interface IP. These predate the AI-driven flow. |
| `/actions/change-hostname` `add-vlan` `set-interface-ip` | `app/actions/<name>/page.tsx` | Individual scenario forms — fill the fields, click Submit, get an APPROVE button. |
| `/preview` | `app/preview/page.tsx` | Legacy deep-link page that showed a proposed action before APPROVE. Removed from sidebar nav — the chat flow now handles approval inline. Lives on as a fallback. |
| `/webui-live` | `app/webui-live/page.tsx` | Debug surface for the WebUI agent — shows the Playwright session phase, screenshot timeline. Not for end users. |

**Components worth knowing** (`frontend/components/`):

| Component | What it does |
|---|---|
| `layout/Sidebar.tsx` | Left nav. Currently groups by feature; the redesign brief should regroup by mental model (OPERATIONS / AUTOMATION / INTELLIGENCE / EXECUTION / DATA & HISTORY / ADMIN — see Workstream 4 in the plan). |
| `layout/TopBar.tsx` | Top bar with backend status indicator. |
| `LiveEventStream.tsx` | The right-hand panel on `/chat` — streams what the agent is doing in real time (each tool call, each iteration). Subscribes to a WebSocket. |
| `preview/ApprovalButtons.tsx` | The APPROVE / REJECT / EXECUTE NOW button trio. **This is the single most safety-critical UI element in the app.** Every router write passes through it. |
| `webui-agent/ActionTimeline.tsx` | When a WebUI flow runs, this shows the screenshots from each Playwright step side-by-side. |
| `webui-agent/PhaseProgress.tsx` | Progress bar for multi-step WebUI flows. |
| `status/BackendStatus.tsx` | Green/red dot showing if the FastAPI backend is reachable. |
| `dashboard/ActionsCount.tsx`, `RecentActions.tsx` | Dashboard widgets summarising recent activity. |

---

## How the user interacts today (the load-bearing flow)

This is the part the redesign must preserve. Cisco's a router — bad UI here means real network outages.

### 1. User types into chat

Plain natural language, mostly Slovak. The chat input lives at the bottom of `/chat`. Examples a user types daily:

- `pridaj VLAN 30 s názvom OFFICE`
- `zmeň hostname na LAB-R1`
- `nakonfiguruj OSPF process 100 area 0 na Vlan1 cez CLI`
- `pridaj statickú trasu 10.99.99.0/24 cez 192.168.10.254 cez WebUI`
- `ako sa konfiguruje trunk port?` (read-only question)

The chat sends `POST /api/chat` and waits for a response. Typical response time: 2-15 seconds.

### 2. AI replies with either:

**(a) An answer** (if it was a question). Markdown, possibly with a **Sources** section at the bottom citing Cisco docs. Nothing to approve.

**(b) An action proposal** (if it was a config request). The reply contains:
- A short Slovak summary of what will happen
- An action ID like `act_20260518_8e18fd`
- For CLI: the exact IOS XE command list + a verify command + a one-line risk note
- For WebUI: a step list ("click Add", "fill Prefix=10.99.99.0", ...) + a verify text + risk note
- The APPROVE / REJECT / EXECUTE NOW button trio (rendered inline via `ApprovalButtons.tsx`)

The user has to click **APPROVE** first, then **EXECUTE NOW**. Both clicks fire separate API calls (`POST /api/approve/{id}` then `POST /api/execute/{id}`). The two-click split is **deliberate friction** — it's our HITL (human-in-the-loop) gate. The router never gets touched until both buttons have been pressed.

A REJECT click sends `POST /api/reject/{id}` and the action moves to REJECTED state, no router contact.

### 3. While the action runs

A **live event stream** appears in a right-hand panel (`LiveEventStream.tsx`). It subscribes to `ws://localhost:8000/ws/agent` and renders structured log lines as they happen. Looks like:

```
LIVE EVENT STREAM · /WS/AGENT
thinking · iter 0
→ search_docs({"query":"static route WebUI"})
✓ search_docs
thinking · iter 1
→ propose_webui_configure({intent: "add static route...", webui_path: "/webui/#/staticRouting"})
✓ propose_webui_configure
⏸ awaiting_approval · act_20260518_abc123
[user clicks APPROVE + EXECUTE]
▶ EXECUTE → webui_configure
  iter 1: click Add
  iter 2: fill Prefix=10.99.99.0, fill Prefix Mask=255.255.255.0, fill Next Hop=192.168.10.254, click Apply
✓ EXECUTED — action complete
```

For WebUI flows: a real Chromium window opens **in front of the user** during the execute step. They watch the agent click and type. This is intentional — visible evidence beats invisible automation.

### 4. Outcome

After execute, the user sees one of:

- **✓ EXECUTED** — action completed, verify passed. Snapshots saved to disk under `artifacts/device-snapshots/<action_id>/` (CLI) or screenshots under `artifacts/screenshots/<session>/` (WebUI). Both pre and post states are kept for audit.
- **✗ FAILED** — typically `verify_failed`. The chat shows the structured error. If the device rejected a command (e.g., `% Router-ID 10.0.0.1 in use by ospf process 2`), there's a `device_errors` field that should be prominent in the new design — operators currently miss it.
- **Error** — the agent refused, or a tool errored. The chat shows the message and stops. No retry button (deliberate — auto-retry on router writes is forbidden).

---

## Design principles the redesign must respect

These come from the project's safety constraints — they're hard requirements, not preferences.

1. **The APPROVE/EXECUTE split is sacred.** Two physical clicks must remain between intent and side-effect. No "approve and execute" combo button. No keyboard shortcut for execute. No double-click shortcut. The friction is the safety mechanism.

2. **Action IDs must be visible.** `act_20260518_8e18fd` is the audit trail anchor. The user should always be able to see which action ID they're approving and find it again afterward. Don't tuck it behind a hover or a details disclosure.

3. **Per-action evidence must be one click away.** Every executed action has pre/post snapshots (CLI) or screenshots (WebUI) on disk. The current UI surfaces them via the action timeline; the new UI should keep that easily accessible.

4. **Errors get foregrounded, not hidden.** When a write fails, `device_errors` and `verify_output_preview` carry the diagnostic. The current "✗ SERVER ERROR" block shows them; a new design that hides them behind a "details" expander would lose debugging speed.

5. **Live event stream is the trust signal.** Watching the agent's tool calls land in real time is what makes operators trust it on day 2 and beyond. It can move (right panel, bottom drawer, separate page), but it shouldn't disappear.

6. **No surprise auto-actions.** If the chat is showing an awaiting-approval state, NOTHING starts running until the user clicks the buttons. No "auto-approve if confident" toggle. No timer that pre-approves after N seconds.

---

## What's stable vs in flux

| Surface | State | Implication for redesign |
|---|---|---|
| `/chat` core flow | Stable, used daily | Redesign the LOOK, keep the FLOW |
| Live event stream | Stable, depended on | Can move position/style, must remain visible during execute |
| `/actions` scenario forms | Frozen — no new ones planned | Could be archived into a "Quick actions" panel inside `/chat` |
| `/preview` page | Legacy, mostly orphaned | Safe to redesign or merge into chat |
| `/webui-live` debug page | Internal, low traffic | Can be reskinned without much risk |
| Sidebar grouping | Currently feature-flat | Plan calls for regrouping into OPERATIONS / AUTOMATION / INTELLIGENCE / EXECUTION / DATA & HISTORY / ADMIN |
| Approve/Execute buttons | Wired into chat | The UI text/icons can change; the two-click contract cannot |

---

## What the agent does today (so the designer knows what UI is needed)

The agent has three write paths exposed in the chat:

1. **Fast-path CLI tools** — `set_hostname`, `set_interface_ip`, `set_access_vlan`. Single-line config changes. Sub-5-second flows.
2. **Fast-path WebUI tools** — `webui_set_hostname`, `webui_add_access_vlan`. Same outcomes as above but driven through the Cisco WebUI in a visible Chromium window. ~30-second flows. Used for demos and visual confirmation.
3. **Generic configure tools** (added 2026-05-15):
   - `propose_cli_configure` — anything else over SSH (OSPF, BGP, ACLs, route-maps, NAT). The agent drafts the IOS XE commands itself, grounded in the Cisco manual via RAG.
   - `propose_webui_configure` — anything else through the WebUI. Multi-step forms (click Add → fill fields → click Apply) are handled by a "multi-propose chain" that re-describes the page between steps.

The redesign should treat all three as **first-class equals** — currently the fast paths and the generic paths render identically in chat, which is good. The new design should preserve that.

---

## Mockups (if any exist already)

The project plan mentions mockups for Chat / Action Library / Preview Change. If those exist in the designer's tooling (Figma etc.), they should be the starting point. Otherwise this doc is the brief.

---

## Files the designer can browse to see current screens

| To see... | Open this in the running app | Or read this file |
|---|---|---|
| The main chat surface | http://localhost:3000/chat | `frontend/app/chat/page.tsx` |
| APPROVE / EXECUTE buttons | (Trigger any write in chat) | `frontend/components/preview/ApprovalButtons.tsx` |
| Live event stream | Right side of `/chat` | `frontend/components/LiveEventStream.tsx` |
| Sidebar nav (current) | Left side, every page | `frontend/components/layout/Sidebar.tsx` |
| Dashboard widgets | http://localhost:3000/ | `frontend/components/dashboard/` |
| WebUI flow screenshot timeline | http://localhost:3000/webui-live | `frontend/components/webui-agent/ActionTimeline.tsx` |

---

## Questions worth asking the designer back

- **Where does the live event stream go?** Right panel (current) vs bottom drawer vs separate route?
- **How prominent is the action ID?** It's currently a short code chip — keep, expand, or hide behind hover?
- **The Chromium window that opens during WebUI execute** — embed it in the page via an iframe-or-similar? Or keep it as a separate OS window (current behaviour)?
- **Failed-action display** — when a write fails, what hierarchy of info: error reason → device_errors → snapshots → full output?
- **Slovak/English** — most users type Slovak, but the UI strings are English. Mixed deliberate or worth localising?

These are blockers to commit to a final design. The flow is stable; the look is open.
