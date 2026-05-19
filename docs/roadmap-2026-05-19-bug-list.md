# Roadmap from Filip's "what to change.txt" bug list (2026-05-19)

## Context

Filip dropped a full list of bugs and rough edges across the Dashboard, Devices page, AI Configuration page, and Config Preview page. This plan consolidates that list with the carryover chunks from `docs/next-session-kickoff.md` into one ordered roadmap.

**Already shipped today** (don't re-do):
- Chunk 1 — `WriteRejectedError` + post-write verify in `backend/cli_agent/write_tools.py` (commit `942f303`).
- Chunk 1.5 — L2-only switchport pre-check + SVI auto-redirect in `_propose_set_interface_ip` (commit `c9bb895`).

**Three audits informed the plan:**
- Frontend mock-vs-real map: most Dashboard widgets are MOCKED ([frontend/screens-basic.jsx](frontend/screens-basic.jsx) lines 3-137); only Recent Activity is real. Chat state dies on page unmount ([frontend/screen-ai.jsx:83-340](frontend/screen-ai.jsx)). No language detection. Preview page falls back to hardcoded `"Add VLAN 30 named OFFICE on Router-01"` ([frontend/screen-preview.jsx:4-13](frontend/screen-preview.jsx)).
- Backend API gaps: `/api/devices` returns hardcoded mock with `uptime: "—"` ([backend/api/routes_devices.py:13-18](backend/api/routes_devices.py)). `show_version` read tool exists and parses uptime — just not wired. No `last_backup`, `sessions`, or `config_dirty` endpoint.
- LLM bias: [backend/orchestration/planner.py:125-126](backend/orchestration/planner.py:125) literally says *"Speak Slovak by default; switch to English if the user writes in English or asks for it."* The reactive-only switch is why Filip is seeing Slovak replies to English input.

## Summary — ordered roadmap

| # | Chunk | Phase | Est | Pri |
|---|---|---|---|---|
| 1 | **Language fix** — drop "Slovak by default", mirror user's language | B | ~10 min | HIGH |
| 2 | **Chat persistence + reset button** — lift state to React Context, add reset chip | B | ~45 min | HIGH |
| 2b | **Live CLI command stream** — emit a `cli_command_sent` event per command + render in chat | B | ~45 min | HIGH |
| 3 | **Wire /api/devices to `show_version`** — real `ios`, `uptime`, `ip` from one SSH read | A | ~30 min | HIGH |
| 4 | **`/api/devices/<id>/last-backup`** — scan `artifacts/device-snapshots/` mtime | A | ~30 min | MED |
| 5 | **Dashboard goes real** — replace KPIs + Device Overview + Connection Trace card | A | ~60 min | HIGH |
| 6 | **VLAN-exists + hostname-same pre-checks** — mirror chunk 1.5 pattern | C | ~45 min | HIGH |
| 7 | **Inner-planner conflict pre-check** — feed running-config slice; refuse known collisions before propose returns (covers old chunk 7 router-id) | C | ~45 min | MED |
| 8 | **Surface planned commands in fast-path proposals** (old chunk 2) | C | ~20 min | MED |
| 9 | **WS origin allowlist `127.0.0.1`** (old chunk 3) | F | ~5 min | MED |
| 10 | **Anthropic 529 retry hardening** (old chunk 5) | F | ~20 min | MED |
| 11 | **Smart fast actions** — `/api/suggestions` calls Haiku with running-config digest | D | ~60 min | MED |
| 12 | **Auto-debug on failure + on-demand sweep** | G | ~90 min | MED |
| 13 | **Config Preview cleanup** — latest-action default, font fix, role decision | E | ~30 min | MED |
| 14 | **WebUI speed pass** — trim `_settle_page` waits, retest on live router | G | ~60 min | LOW |
| 14b | **Self-training WebUI vision fallback** — on unknown page, Vision API finds the element + saves screenshot + selector to learned knowledge base | G | ~4 h | MED |
| 15 | **Hardware retests** (old chunk 6) — ISIS + OSPF WebUI after chunks 7 & 10 land | F | ~30 min | MED |
| 16 | **README + screenshots + GitHub metadata** (old chunk 8 — manual) | F | ~5 min | — |
| 17 | **Cosmetic prototype-label sweep** (old chunk 9) | F | ~10 min | LOW |
| 18 | **`v0.4.0-alpha.1` consolidation tag** (old chunk 10) | F | ~15 min | — |

---

## Phase A — Dashboard goes real

Filip's bug #1 + #2: replace the hardcoded KPIs, device overview, connection trace, and the active-device info on the Devices page with real data. Keep the multi-device list mocked (Filip said so explicitly).

**Chunk 3 — wire `/api/devices` to real device state**
- Edit [backend/api/routes_devices.py:9-26](backend/api/routes_devices.py): inside the handler, call `read_tools.show_version()` and `read_tools.show_ip_interface_brief()` once; map `version`, `uptime` from the show_version dict, and the management IP from the interface brief. Fall back to current static values on SSH error (don't break the page).
- Keep the single-device hardcoded `id: "router-01"` shape — multi-device discovery is out of scope per Filip's note.

**Chunk 4 — `/api/devices/<id>/last-backup`**
- New endpoint in `routes_devices.py`: scan `artifacts/device-snapshots/` for the most-recent `post/` subdirectory mtime, return `{action_id, taken_at, snapshot_path}`.

**Chunk 5 — Dashboard widgets to real**
- Edit [frontend/screens-basic.jsx](frontend/screens-basic.jsx) (lines 3-137):
  - 4 KPI cards: `Devices connected` from `fetchDevices().length`; `Configs saved` from a new lightweight `/api/snapshots/count` (or just count `artifacts/device-snapshots/` entries client-side via the same endpoint); `Active sessions` — defer (needs new `show users`/`show ssh` CLI tool; mark "—" with a tooltip); `Health` derived from whether the last action succeeded (read from `/api/logs/recent`).
  - Device Overview card: pull from `fetchDevices()[0]` and the new `last-backup` endpoint.
  - Connection Trace card: replace the hardcoded list with the last 5 entries from `/api/logs/recent` filtered to network events (`show_*`, `set_*`, `cli_configure`).

## Phase B — Chat UX (highest visible-pain items)

**Chunk 1 — language fix**
- Edit [backend/orchestration/planner.py:125-126](backend/orchestration/planner.py:125): replace *"Speak Slovak by default; switch to English if the user writes in English or asks for it."* with *"Detect the language of the user's most recent message and reply in that same language. Default to English if detection is ambiguous."*
- Add a 1-2 sentence test prompt in `tests/unit/test_planner.py` — pass an English user message, mock the Anthropic call, assert the system prompt no longer contains "Slovak by default".

**Chunk 2 — chat persistence + reset button**
- Lift `messages`, `chatHistory`, `pending`, `stream`, `phase` out of [frontend/screen-ai.jsx:83-90](frontend/screen-ai.jsx:83) into a new `ChatContext` (React Context Provider mounted at app root in `frontend/app.jsx`).
- Add a small "Reset chat" chip in the chat header that clears all five fields. Confirm via a 1-click toast (no modal — Filip wants fast).
- Navigation away from AI Config page no longer unmounts state; returning shows the prior conversation.

**Chunk 2b — live CLI command stream**
- Filip wants to see exactly what the agent is typing at the IOS prompt as it goes. Today the chat shows the approval preview and the final result but nothing in between.
- Backend: in each write tool in [backend/cli_agent/write_tools.py](backend/cli_agent/write_tools.py) (`set_hostname`, `set_interface_ip`, `set_access_vlan`, `cli_configure`), iterate over the `config_commands` list just before `send_config_set(...)` and emit a `cli_command_sent` event per command via the existing eventbus (used by the WS at `/ws/agent`). Event shape: `{type: "cli_command_sent", tool, action_id, command, command_index, command_total, mode: "config" | "exec"}`. Same emit for the post-write verify `send_command(...)` call (mode `exec`).
- Frontend: in [frontend/screen-ai.jsx](frontend/screen-ai.jsx) extend the event-stream renderer (around `synthesizeProposal` and the stream list) to handle the new event type — render as a single code-styled line `c1111-lab(config-if)# ip address 192.168.40.1 255.255.255.0` so it reads like a terminal scroll. Use a small 100-150 ms client-side delay between consecutive lines so the user perceives the cadence (the actual SSH is a single round-trip; this is cosmetic pacing).
- This is light-touch — no Netmiko refactor. If we later want true per-command pause/rollback, that's a separate chunk that loops with `send_command_timing` instead of `send_config_set`.

## Phase C — Smart pre-checks at propose time

**Chunk 6 — duplicate-config detection**
- `_propose_set_access_vlan` ([backend/orchestration/tool_registry.py:553](backend/orchestration/tool_registry.py:553)): call new `read_tools.show_vlan_brief()` slice (already exists at [read_tools.py:94](backend/cli_agent/read_tools.py:94)). If VLAN id already exists, return a `vlan_exists` warning result with the current name and let the operator decide ("VLAN 30 already exists as 'OLD_NAME' — replace with 'OFFICE'?").
- `_propose_set_hostname` ([tool_registry.py:510](backend/orchestration/tool_registry.py:510)): if current hostname matches requested, refuse with `hostname_unchanged` so we don't waste an approval round-trip.
- Mirror the same SSH-soft-fail pattern from chunk 1.5: any read error falls through to the existing propose.

**Chunk 7 — inner-planner conflict pre-check** (consumes old kickoff chunk 7)
- In `cli_configure_planner.draft_cli_plan` ([backend/orchestration/cli_configure_planner.py](backend/orchestration/cli_configure_planner.py)): after the inner Haiku drafts commands but before `_propose_cli_configure` returns, run a small server-side scan: if the drafted plan contains `router ospf N` / `router bgp N` / `router-id <x>`, grep the running-config slice for the same and refuse with a structured `conflict_detected` result naming the existing config.
- `_propose_webui_configure` ([tool_registry.py:875](backend/orchestration/tool_registry.py:875)) currently doesn't read running-config; add a single `show_running_config()` call so the inner Haiku at least has device state context.

**Chunk 8 — surface planned commands in fast-path proposals** (old kickoff chunk 2)
- Each `propose_set_*` in [tool_registry.py](backend/orchestration/tool_registry.py) should include the exact CLI lines its write_tool will run in a `commands` field. Frontend's `synthesizeProposal` already looks for `input.commands` and shows them.

## Phase D — Smart fast actions (the chat suggestion chips)

**Chunk 11 — context-aware suggestions**
- Replace the 4 static lines at [frontend/screen-ai.jsx:189-194](frontend/screen-ai.jsx:189) with a fetch to a new `GET /api/suggestions` endpoint.
- New endpoint: pulls a running-config digest (filter to hostname + VLAN list + interface IPs), passes to Haiku with a tight system prompt "Suggest 4-6 short user prompts the operator might want next, given this device state. One per line, under 12 words each, in <user language>. Avoid suggesting things already present."
- Cache the result for 30s per-device to avoid hammering Haiku on every chat-page render.

## Phase E — Config Preview cleanup

**Chunk 13 — preview page polish**
- [frontend/screen-preview.jsx:4-13](frontend/screen-preview.jsx:4): when no `actionId` is in the route, fetch the most-recently-executed action's id from `/api/logs/recent` and show ITS snapshot, not the hardcoded `"Add VLAN 30 named OFFICE on Router-01"` placeholder.
- Font mismatch on "Running config — before" vs "— after" labels: hunt down the CSS in [frontend/styles.css](frontend/styles.css), unify under the existing `card--diff` class.
- Decide on the page's role: keep as "always-on diff viewer for the most recent action" (recommended — useful for post-write inspection), or hide from nav. Recommended: keep it, just make it never show stale placeholder text.

## Phase F — Carryover hygiene (small, fast)

| Chunk | Detail | Source |
|---|---|---|
| 9 | Add `"http://127.0.0.1:8000"` to `allowed_origins` in [backend/core/settings.py](backend/core/settings.py) | old chunk 3 |
| 10 | `max_retries=5` on Anthropic clients in [planner.py](backend/orchestration/planner.py), [configure_planner.py](backend/orchestration/configure_planner.py), [cli_configure_planner.py](backend/orchestration/cli_configure_planner.py); wrap `OverloadedError` -> friendly dict | old chunk 5 |
| 15 | Re-run ISIS + OSPF WebUI flows on the live router after chunks 7 & 10 land | old chunk 6 |
| 16 | Filip's manual step: drop 3 PNGs to `docs/screenshots/`, push README rewrite + GitHub repo metadata | old chunk 8 |
| 17 | Strip "Prototype" label from `frontend/README.md`, `frontend/index.html`, `frontend/styles.css` header | old chunk 9 |
| 18 | Cut clean `v0.4.0-alpha.1` once chunks 6, 7, 10 land | old chunk 10 |

## Phase G — Auto-debug + WebUI speed

**Chunk 12 — auto-debug (both modes per Filip's confirmation)**
- **Reactive (on write failure):** when a write_tool raises `WriteRejectedError` or the executor catches `tool_failed`, the outer planner picks up the error envelope and automatically asks Haiku to draft a diagnostic plan (one or two `show` commands) tailored to the failure signature. Operator sees a follow-up proposal: "Diagnose: run `show ip interface brief` + `show vlan brief`?" — approve once, get a plain-English explanation.
- **On-demand sweep:** new chat intent (matched as a fast action chip: "Debug my config") that runs a curated `show` block (running vs startup-config diff, `show interfaces status`, `show logging` tail-50) through Haiku for a digest. Output rendered as a single chat message with sections.
- Implementation: new `_propose_debug_sweep` in [tool_registry.py](backend/orchestration/tool_registry.py); the reactive variant is a small change in the outer planner's tool-error handler.

**Chunk 14 — WebUI speed pass** (Filip picked "trim `_settle_page` waits")
- Audit every `_settle_page` / `wait_for_*` / `page.wait_for_timeout` call in `backend/webui_agent/`. The current waits were calibrated conservatively for the alpha.4 modal-race fix.
- For each: drop to the empirical minimum (re-test on live router), or replace fixed timeouts with `wait_for_selector(..., state="visible", timeout=3000)` patterns that fail fast and retry once instead of always waiting.
- Smoke evidence to `artifacts/screenshots/...` per usual convention.

**Chunk 14b — self-training WebUI vision fallback** (Filip's "smarter but still controlled" ask)
- Today the WebUI agent only knows what's in [backend/webui_agent/selectors/iosxe_default.yaml](backend/webui_agent/selectors/iosxe_default.yaml). When an operator drives it to a page that isn't in the YAML, it gets confused. The fix is a vision-assisted exploration loop with persistence — keep the human-in-the-loop approval for writes, but let the navigation/discovery be autonomous and learn over time.
- Loop, applied in [backend/webui_agent/generic_driver.py](backend/webui_agent/generic_driver.py) every time we'd otherwise raise "selector not found":
  1. Try the learned selectors first (new file `backend/webui_agent/selectors/learned.yaml`), then the default YAML.
  2. On miss: take a Playwright screenshot, send it + the natural-language intent (e.g. "find the 'Add' button on the Static Routes page") to Claude via the **image-content-block** API (Anthropic Vision is just a content type, not a separate model — Haiku 4.5 already supports it, no infra change).
  3. Vision returns either a CSS selector candidate or click coordinates. Try the selector first; fall back to `page.mouse.click(x, y)` if needed.
  4. On success: append to `learned.yaml` (`page_url -> {intent, selector, screenshot_path, learned_at}`) and save the screenshot to `artifacts/webui-learned/<page-hash>/<intent-slug>.png`.
- Surface to the chat: emit a `webui_vision_used` event so the operator sees "agent didn't know this page — used Vision, learned new selector for 'Add VLAN'" in the live event stream.
- RAG integration: ingest each `learned.yaml` entry into the existing ChromaDB vectorstore (small ingest job in `backend/knowledge_agent/ingest.py`) so the outer planner's `search_docs` can find it when the user later asks about that page.
- Guardrails (the "still controlled" part): vision fallback only fires for **read/navigation** steps (click a menu item, fill a form field) — never to bypass an approval gate. Write-tool execution still flows through the existing `propose_*` + approval path.
- Tests: mock the Anthropic message create with a fake image-response; assert (a) learned.yaml grows by one entry, (b) screenshot file is written, (c) second visit to the same page reads from learned.yaml and skips the vision call entirely.
- Risk: image tokens are ~1.5 cents per screenshot; capping per-session vision calls at 5 (configurable) prevents runaway spend on a misconfigured router. Filip's note "it cost nearly any credits" applies — but a cap is still cheap insurance.

---

## Verification

Per-chunk verification is inline above. End-of-roadmap checks:
- `.venv\Scripts\python.exe -m pytest tests -m "not integration and not slow"` should still pass (currently 534/534).
- Live smoke through the chat at `http://localhost:8000/`: ask in English ("set hostname c1111-lab") — expect English reply (chunk 1). Ask in Slovak ("nastav hostname na c1111-lab") — expect Slovak reply. Navigate to Devices then back to AI Config — chat history is still there (chunk 2). Open Dashboard — all four KPIs show real numbers (chunk 5). Click "Reset chat" — conversation clears (chunk 2). Try `propose_set_access_vlan` for an existing VLAN id — see the `vlan_exists` warning (chunk 6).
- Tag `v0.4.0-alpha.1` (manual by Filip) once chunks 6, 7, 10 are green.

## Notes

- Chunks are ordered by impact x cheapness. If you want to surface the language fix or chat persistence even sooner (they're the most visible UX wins and the smallest changes), I can land 1 + 2 in a single tight session before touching the dashboard.
- Old kickoff chunk 4 (sidebar + Dashboard device-overview wiring) is FULLY absorbed by chunks 3 + 5 above.
- Old kickoff chunk 7 (router-id pre-check) is FULLY absorbed by chunk 7 above.
- Filip's "is that real activity real?" check from the Dashboard bullet: yes — Recent Activity already reads from `/api/logs/recent` via `window.api.fetchRecentActivity(10)` ([screens-basic.jsx:60-72](frontend/screens-basic.jsx:60)). That's the one Dashboard widget already real.
