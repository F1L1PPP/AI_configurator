# Next session kickoff — 2026-05-21+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. You are operating as the **Orchestrator / Head Architect** of an Engineering & Networking Team reporting to the Director (Filip). Read the Director Blueprint and the roadmap before responding:

1. [CLAUDE.md](CLAUDE.md) — project tone, branch rules, commits, AND the **Communication style** section (team voice, tradeoffs first, no fluff).
2. [~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md](~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md) — full Director Blueprint (role split, communication, concrete flow per task, edge cases).
3. [docs/roadmap-2026-05-19-bug-list.md](docs/roadmap-2026-05-19-bug-list.md) — master 18-chunk roadmap from Filip's bug list. Phases A, B, C, D, E + chunks 1/1.5/9/10 are LANDED.
4. The "What landed 2026-05-19" + "What landed 2026-05-20" sections in **this** kickoff doc — chronological recap of the last two days.

After reading, summarise back in 6-8 sentences:

1. `feature/bootstrap` at HEAD `88bd731`, 588 tests passing (+44 over the 544 baseline of 2026-05-20 morning), last tag `v0.5.5-phase-e-preview-diff` at `88bd731`.
2. The Director Blueprint operating directive — team voice ("Team recommendation:" / "We should…"), tradeoff tables BEFORE architectural decisions, Haiku as delegated read-fetcher, reject corporate fluff.
3. Phases A, B, C, D, E + chunks 1/1.5/9/10 are LANDED. Don't re-do.
4. Phase C went BEYOND the original chunks: also covers WebUI fast-path conflict detection + a C1111-4P-specific `show_vlan_brief` fallback (VLAN definitions live in vlan.dat, not running-config on this chassis).
5. Phase E (`v0.5.5-phase-e-preview-diff`) made the Config Preview's diff render for real via a new `/api/actions/{id}/snapshot/{phase}` endpoint that bridges the backend-stores-paths / frontend-expects-content gap that had been there since day one.
6. The Opus → Sonnet → Haiku agent split is the standard flow: Opus writes per-chunk briefings inline; Sonnet implements with tests interleaved; Haiku audits deltas.
7. Working tree: README.md modification still uncommitted (waiting on screenshots — chunk 16).
8. Next candidate: chunk 12 (auto-debug on write failure + on-demand "Debug my config" sweep, ~90 min, MED priority). Skip the chunk-order question — it is locked unless Filip asks.

Then wait for "go" before making any change. **Do not propose re-planning the chunk order** — it is locked unless Filip asks.

=== END ===

---

## What landed 2026-05-19

Nine commits, four tags, +21 regression tests (523 → 544 passing). All on `feature/bootstrap`.

### Roadmap reconstitution (morning)

Filip dropped `what to change.txt` listing bugs across Dashboard, Devices, AI Configuration, Config Preview. Three Explore agents in parallel mapped frontend mocks-vs-real, backend API gaps, and LLM language bias. Output: [docs/roadmap-2026-05-19-bug-list.md](docs/roadmap-2026-05-19-bug-list.md) — 18 ordered chunks across 7 phases.

### Chunk 1 — `WriteRejectedError` + post-write verify (`942f303`)

Silent-failure bug from 2026-05-18: `set_interface_ip` on Gi0/1/3 looked successful in chat but the IP never reached the router (hardware L2-only port + no output validation + no show-back). Two-layer fix in [backend/cli_agent/write_tools.py](backend/cli_agent/write_tools.py):
- `_check_netmiko_output_for_errors` — scans `send_config_set` output for `%` error lines, raises `WriteRejectedError`.
- `_verify_running_config` — re-fetches `show running-config | include hostname` / `show running-config interface X` / `show vlan brief` and asserts the change actually landed.

Forensic post-snapshot on failure; never auto-retries. +5 regression tests. Docs: [docs/router-prerequisites.md](docs/router-prerequisites.md) gets the C1111-4P Gi0/1/x L2-only quirk.

### Chunk 1.5 — SVI auto-redirect (`c9bb895`)

Director's expansion to chunk 1: instead of surfacing the L2-only failure as a hard error, the agent should re-route. `_propose_set_interface_ip` in [backend/orchestration/tool_registry.py](backend/orchestration/tool_registry.py) now does a `show running-config interface <name>` pre-check. If the port is a switchport, builds a deterministic 3-block SVI plan (VLAN N + SVI ip + switchport access vlan N) and routes through `cli_configure`. VLAN id derived from third octet of IP (192.168.40.1 → VLAN 40; falls back to 100 for VLAN-1/zero-octet cases). +6 regression tests. New helper `read_tools.show_running_config_interface`.

### Phase A — Dashboard goes real (`3d0f27c` + `849c210`)

Filip's bug #1: KPIs and Device Overview card were hardcoded HTML.
- **Chunk 3**: [backend/api/routes_devices.py](backend/api/routes_devices.py) — `/api/devices` calls `read_tools.show_version()` and reads `settings.router_host`. Populates `ios`, `uptime`, `ip` with real values; SSH soft-fail to static fallback.
- **Chunk 4**: New `GET /api/devices/<id>/last-backup` — scans `artifacts/device-snapshots/*/post` for freshest mtime. Returns `{action_id, taken_at, snapshot_path, count}` — `count` drives the "Configs saved" KPI.
- **Chunk 5**: [frontend/screens-basic.jsx](frontend/screens-basic.jsx) `DashboardScreen` rewrite. KPIs, Device Overview card, and Connection Trace card all consume real data. New `relativeTime()` helper for "Xm ago" rendering. Connection Trace mirrors last 5 entries from `/api/logs/recent`. "Active sessions" KPI shows `—` (deferred; no CLI tool for `show users`/`show ssh` yet).
- **Sidebar fix** ([frontend/chrome.jsx](frontend/chrome.jsx)): `ACTIVE DEVICE` card now fetches from `/api/devices` instead of hardcoded `192.168.1.1` / `ISR 4321`.

+5 regression tests. **Tag: `v0.5.1-phase-a-dashboard`** (annotated). **Backup: `backup-20260519-0859`**.

### Phase B — Chat UX (`059e668`)

Three chunks in one commit.

- **Chunk 1 — language fix**: [backend/orchestration/planner.py:125-126](backend/orchestration/planner.py:125) — replaced *"Speak Slovak by default; switch to English if the user writes in English or asks for it."* with a symmetric *"Detect the language of the user's most recent message and reply in that same language."* Also translated the Slovak `**Bezpečnosť:**` safety paragraph. Kept Slovak intent-recognition keywords (`vykonaj`, `cez WebUI`, `schválená`) — those are USER input triggers, not output bias.
- **Chunk 2 — chat persistence + reset button**: New `ChatProvider` in [frontend/app.jsx](frontend/app.jsx) lifts six chat state slices (`messages`, `pending`, `stream`, `phase`, `history`, `chatHistory`) plus the WS subscription into a Context at app root. Navigation between pages no longer drops the conversation. "Reset chat" chip in the chat header clears all six in one click; disabled mid-flow. Stream capped at 200 lines.
- **Chunk 2b — live CLI command stream**: New `_emit_cli_commands` helper in [backend/cli_agent/write_tools.py](backend/cli_agent/write_tools.py) publishes one `cli_command_sent` event per IOS line via the eventbus, plus one for the post-write `show ...` verify (mode: `"config"` | `"exec"`). Frontend renders as `(config)# interface Gi0/1/3` and `# show vlan brief` terminal-style lines via a 120 ms client-side pacing queue. Reset chat drains the queue. New `.stream-line--cli` style.

+5 regression tests. **Tag: `v0.5.2-phase-b-chat`** (annotated, at HEAD after chunk 9). **Backup: `backup-20260519-0935`**.

### Chunk 9 — WS origin allowlist (`cf3572d`)

Surfaced when Phase B chunk 2b shipped and the live stream still showed "Waiting for agent activity." even though events were being published. Root cause: Filip's bookmarks use `http://127.0.0.1:8000/` but `settings.allowed_origins` only had `localhost` variants. Every WS handshake at `/ws/agent` got a 1008 policy-violation close. One-line fix in [backend/core/settings.py](backend/core/settings.py). Confirmed live: paced `(config)# hostname LAB` and `# show running-config | include hostname` now scroll in the right column on both fast-path and `cli_configure` flows (tested with hostname change + OSPF process 1).

### Director Blueprint (`c0da7e3`)

Filip's 2026-05-19 directive: the agents are not independent — they form a specialized engineering team reporting to the Director. Codified in two places:
- [CLAUDE.md](CLAUDE.md) gets a new `## Communication style` section: team voice ("Team recommendation:" / "We should…"), tradeoff tables BEFORE architectural decisions, cross-reference to the memory file.
- `~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md` (user-home, not in repo) — full rewrite as the Director Blueprint. Preserves the role-split flow + edge cases. Adds the four new rules. Director's React-vs-Next.js example included as the reference tradeoff-table format.

---

## What landed 2026-05-20

24 commits across `feature/bootstrap` between morning HEAD `c0da7e3` and end-of-day HEAD `88bd731`. 6 tags pushed (3 phase milestones + 3 timestamped backups). Tests 544 → 588 (+44), 0 failed. Live-smoked on the C1111-4P throughout.

### Phase C — Universal conflict pre-checks (`v0.5.3-phase-c-conflict-detect` at `f0d788a`)

**Chunk 6 (`040affc`)** — `backend/orchestration/conflict_detector.py` with `find_existing_block(commands, running_config) -> {anchor, block, is_exact_match} | None`. Universal anchor algorithm handles indented stanzas (vlan/interface Vlan/router/route-map/etc.) AND single-line globals (hostname, ip route). Physical-interface guard, `no <X>` skip, `is_exact_match` for true no-op detection. 13 unit tests.

**Chunk 7 (`558018e`)** — wired into all 5 CLI propose tools (`set_hostname`, `set_access_vlan`, `set_interface_ip` non-SVI branch, `cli_configure`, `webui_configure`). `configure_planner.draft_plan` extended with optional `running_config` + `equivalent_cli_commands` return so the WebUI inner Haiku also feeds the detector. 8 integration tests.

**Chunk 8 (`cd135b0`)** — frontend: `synthesizeProposal` extracts conflict fields; `ProposalBubble` renders amber `REPLACES EXISTING` / stronger `IDENTICAL CONFIG — APPLYING WILL BE A NO-OP` block above the commands; CSS via `var(--warn)` tokens + `color-mix()` for the noop variant.

### Phase D — Smart suggestion chips (`v0.5.4-phase-d-smart-suggestions` at `2b8dd5d`)

**Chunk 11 (`2b8dd5d`)** — NEW `GET /api/suggestions` endpoint. `_build_digest` extracts hostname + VLAN list + interface IPs from running-config (caps at 2 KB). Haiku 4.5 drafts 4 short chips grounded on the digest. 30s per-device in-memory cache. Soft-fails to 4 static defaults on any failure (SSH down, Haiku overloaded, empty response). Frontend `useState`-seeded with the defaults so the UI is never empty during in-flight fetch. 6 unit tests.

### Phase E — Config Preview real diff (`v0.5.5-phase-e-preview-diff` at `88bd731`)

**Chunk 13 (`596be37`)** — NEW `GET /api/actions/{action_id}/snapshot/{phase}` returns `running-config.txt` content. Path-traversal guards (`^act_[A-Za-z0-9_-]+$` regex + phase ∈ `{pre, post}` literal check before any filesystem touch). `fetchPreview` parallel-fetches both snapshots via `Promise.all` and inlines content for the unchanged `adaptPreview` — so the diff finally renders for real. `screen-preview.jsx` drops the hardcoded `"Add VLAN 30 named OFFICE on Router-01"` demo, auto-fetches the most-recent action via `fetchLastBackup` when no `actionId` in route. Change Summary + Commands cards read from `action.params`. 5 unit tests. Plus 6 stacked CSS fixes from live smoke (empty-state monospace + 20px alignment to match card title + viewport-anchor wave via `margin-top: auto`).

### Carryover chunks pulled forward

**Chunk 10 (`dcbe9de`)** — Anthropic 529 hardening. `max_retries=5` on all three Anthropic clients (planner + cli_configure_planner + configure_planner). `routes_chat.py` catches `OverloadedError` specifically → HTTP 503 with `"Claude API is temporarily overloaded (HTTP 529). Already retried 5 times via the SDK. Please wait a minute and try again. request_id: req_..."`. Propose tools wrap inner-LLM overloads into `{"error": "llm_overloaded", ...}` structured dicts that the executor maps to 503. 5 unit tests. **Live-verified** during Anthropic's actual overload window today.

### Bug-fix commits stacked on Phase C/E (lessons learned)

- `9fcaa9d` — `preview_meta` separated from `action.params` (executor splat broke `set_hostname` when chunk 7 stuffed conflict fields into params; architectural fix, not pragmatic patch).
- `bc10d5a` — `commands` routed through `awaiting_approval` event instead of `tool_call.input` (frontend was reading the wrong event source).
- `d204e3f` — `_event_to_dict` emits `{type, data}` instead of `{kind, data}` to match the WS convention. **Latent since day one** — `synthesizeProposal` had never found any awaiting_approval event before this; my Phase C work made the symptom visible.
- `a21ee79` — `synthesizeProposal` type-checks `preview` before assigning to summary string (cli_configure + SVI return preview as a DICT, which crashed React with "Objects are not valid as a React child" — also latent, surfaced after the event-key fix).
- `3873dba` — `/api/devices` enrichment now extracts `hostname` from `show_version`; AI Config page header wires to `/api/devices` instead of hardcoded `"Router-01 · 192.168.1.1"`.
- `3e81818` + `68a50f3` — C1111-4P specific: VLAN definitions live in vlan.dat and don't appear in `show running-config` as a clean stanza, so the universal detector misses them. Added `show_vlan_brief` fallback inside `_propose_set_access_vlan` (later refactored into shared `_detect_vlan_conflict` helper). Also fixed `vlan_name` field name (ntc-templates emits `vlan_name` not `name` — same gotcha the WebUI verify layer had already documented).
- `f0d788a` — `_detect_hostname_conflict` + `_detect_vlan_conflict` extracted as shared helpers so CLI fast-path AND WebUI fast-path (`webui_set_hostname` + `webui_add_access_vlan`) use identical detection logic. Adds `commands` field to WebUI fast-path returns so the IOS XE commands block renders for WebUI proposals too.
- `219a7dc` — `cli_configure` verify_failed now includes a human-readable `message` field (incidental from a smoke test where the chat showed "no message").

### Architectural lessons captured

- **`params` dict is splatted into the executor — never put display-only fields there.** Use `preview_meta` (added to `propose_action` in `confirmations.py`) for propose-time metadata that the UI needs but the write tool doesn't.
- **Event-payload key names must match what the frontend reads.** Chat-reply events now use `type` everywhere (matches `/ws/agent` convention).
- **Mocks must match real parser output.** Three bugs surfaced from mocks using `name` instead of `vlan_name` (ntc-templates field).
- **The CLAUDE.md "tags hands-off" rule was overridden 3× today** with explicit Filip authorisation per phase. Default remains: don't tag unless he says so.

## Remaining chunks (one-line each)

| # | Chunk | Phase | Est | Pri |
|---|---|---|---|---|
| 12 | Auto-debug — reactive on write failure + on-demand sweep | G | ~90 min | MED |
| 14 | WebUI speed pass — trim `_settle_page` waits, retest on live router | G | ~60 min | LOW |
| 14b | Self-training WebUI vision fallback — Claude Vision + learned selectors | G | ~4 h | MED |
| 15 | Hardware retests — ISIS + OSPF WebUI on live router | F | ~30 min | MED |
| 16 | README + screenshots + GitHub metadata (manual — Filip drops 3 PNGs) | F | ~5 min | — |
| 17 | Cosmetic prototype-label sweep | F | ~10 min | LOW |
| 18 | Cut clean `v0.4.0-alpha.1` consolidation tag | F | ~15 min | — |

## Notes / housekeeping

- README.md modification still uncommitted in the working tree (waiting on the 3 PNGs from chunk 16).
- `frontend-design-backup/` sweep is its own commit later in the week (after Phase C feels stable).
- `tools/check_vectorstore.py` and `tools/query_rag.py` flagged in yesterday's dead-code audit as worth a follow-up review — not blocking.
- Director Blueprint applies from message #1 of the next chat. Use team voice, lead with tradeoffs on architectural decisions, route implementation through Sonnet and audits through Haiku.
- The plan file `~/.claude/plans/write-me-what-is-graceful-sparkle.md` is outside the repo; safe to delete after the next session starts since the roadmap and per-chunk briefings live in `docs/`.

## Post-roadmap polish (after all phases land)

Items deferred until the main roadmap (chunks 12 → 18) is complete. None blocking — pick up only when there's appetite for polish work.

- **Wire `device.id` selection into `fetchSuggestions`** — `frontend/screen-ai.jsx:232-244` currently calls `window.api.fetchSuggestions()` with no argument, so the server defaults to `device_id="router-01"`. Today this is correct (single-device C1111-4P lab; sidebar reads `fetchDevices()[0]`, no picker UI). When/if multi-device discovery lands, the device-picker chunk should ALSO wire its selected `device.id` into `fetchSuggestions(deviceId)` and add the selected device to the `useEffect` dependency array so chips refresh on device switch. The cache key in `backend/api/routes_suggestions.py` is already keyed by `device_id`, so backend is forward-compatible. Source: spawned-task chip from chunk 11 implementation; reviewed and deferred 2026-05-20 — premise required a device-selection UI that doesn't exist yet.
- **Tighten Haiku suggestion grounding** — Phase D chip "Enable OSPF routing protocol on this router" surfaced live even though OSPF process 1 is already configured. The `_build_digest` in `routes_suggestions.py` includes `router ospf 1` lines but Haiku doesn't always treat them as exclusions. Consider an explicit `OSPF: process N active` digest line (similar to how VLANs get `vlan N name X`) so the inner system prompt's "avoid suggesting things already present" rule has a clearer signal. ~15 min when revisited.
