# Next session kickoff — 2026-05-20+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. You are operating as the **Orchestrator / Head Architect** of an Engineering & Networking Team reporting to the Director (Filip). Read the Director Blueprint and the roadmap before responding:

1. [CLAUDE.md](CLAUDE.md) — project tone, branch rules, commits, AND the new **Communication style** section (team voice, tradeoffs first, no fluff).
2. [~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md](~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md) — full Director Blueprint (role split, communication, concrete flow per task, edge cases).
3. [docs/roadmap-2026-05-19-bug-list.md](docs/roadmap-2026-05-19-bug-list.md) — master 18-chunk roadmap from Filip's bug list. Phase A and Phase B are done; Phase C is next.
4. The "What landed 2026-05-19" section in **this** kickoff doc — full chronological recap of yesterday's nine commits.

After reading, summarise back in 6-8 sentences:

1. `feature/bootstrap` at HEAD `c0da7e3`, 544 tests passing, last tag `v0.5.2-phase-b-chat` at `cf3572d`.
2. The Director Blueprint operating directive — team voice ("Team recommendation:" / "We should…"), tradeoff tables BEFORE architectural decisions, Haiku as delegated read-fetcher, reject corporate fluff.
3. Phase A (dashboard goes real), Phase B (language fix + chat persistence + live CLI stream), and chunks 1/1.5/9 are LANDED. Don't re-do.
4. Phase C is the next focus: universal "config already exists" pre-check at propose time — not just VLAN, ANY stanza (OSPF, RIP, BGP, route-maps, ACLs, etc.). Architecture is already designed in the roadmap doc.
5. The Phase C plan calls for the **Opus → Sonnet → Haiku** agent split: Opus (this chat) writes per-chunk briefings inline; Sonnet implements with tests interleaved; Haiku audits deltas.
6. Tradeoff already settled for Phase C: **WARN, don't REFUSE.** Pre-check is informational — operator sees existing block + drafted commands side by side and approves with eyes open.
7. Working tree: README.md modification still uncommitted (waiting on screenshots — chunk 16).

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

## Tomorrow's first chunk — Phase C chunk 6: Fast-path conflict pre-checks (~45 min, HIGH)

**Why FIRST**: the operator currently has no signal at propose time when a fast-path write will collide with existing config. "add VLAN 30 named NEW" when VLAN 30 already exists as OLD goes straight to approval, then either silently updates or surprises the operator. Same for setting the hostname to the current value (wasted approval round-trip).

Two propose tools in [backend/orchestration/tool_registry.py](backend/orchestration/tool_registry.py):

1. **`_propose_set_hostname`** ([tool_registry.py:510-528](backend/orchestration/tool_registry.py:510)): read current hostname via `read_tools.show_version().get("hostname")`; if requested == current, return `{"error": "hostname_unchanged", "message": "Router is already named <X>.", "current_hostname": "<X>"}`. SSH soft-fail → fall through to normal propose.

2. **`_propose_set_access_vlan`** ([tool_registry.py:553-571](backend/orchestration/tool_registry.py:553)): call `read_tools.show_vlan_brief()`, scan for `vlan_id` match; if present, add `existing_entity: "vlan <id>"` + `existing_block: "<id> <current_name> <status>"` to the proposal preview. **Never refuse** — VLAN rename is a legitimate use case. SSH soft-fail same pattern.

**Tests**: 4 regression tests covering: hostname-same refuses; hostname-different proceeds; VLAN-exists adds `existing_entity`; SSH read failure → soft-fall to normal propose.

**Run as**: Opus (the next chat) writes the per-chunk Sonnet briefing inline with the relevant slice of [docs/roadmap-2026-05-19-bug-list.md](docs/roadmap-2026-05-19-bug-list.md) Phase C section. Sonnet implements with tests interleaved. Haiku audits the delta. Director (Filip) only re-reviews if Haiku flags divergence.

**No tag after this chunk** — collect chunks 6+7+8 under one Phase C tag.

## Chunk 7 — Universal `cli_configure` conflict pre-check (~60 min, HIGH)

**NEW module**: `backend/orchestration/conflict_detector.py` (~120 lines). Public API:
```python
def find_existing_block(config_commands: list[str], running_config: str) -> dict | None
```

Universal anchor algorithm (see roadmap doc Phase C "Architecture" section for the full spec): extract the first non-trivial line from `config_commands`, skip if `no <anything>`, classify (physical interface → skip; virtual interface or top-level stanza → check), regex-search running-config for the anchor, walk forward to extract the indented block. Returns `{"anchor": "router ospf 1", "block": "router ospf 1\n network ...\n exit"}` or `None`.

Wire into:
- `_propose_cli_configure` ([tool_registry.py:770-794](backend/orchestration/tool_registry.py:770)) — after `draft_cli_plan` returns, before validators. Inject `existing_entity` + `existing_block` into proposal preview when not None.
- `_propose_webui_configure` ([tool_registry.py:906-910](backend/orchestration/tool_registry.py:906)) — also gets `show_running_config()` context (currently drafts blind). Update `draft_plan` signature in [backend/orchestration/configure_planner.py](backend/orchestration/configure_planner.py) to accept optional `running_config: str = ""`.

**NEW tests**: `tests/unit/test_conflict_detector.py` (~10 unit tests) + 2 integration tests in `tests/unit/test_tool_registry_phase5.py`.

## Chunk 8 — Surface commands in fast-path proposals + frontend rendering (~30 min, MED)

- Add `commands` field to `_propose_set_hostname`, `_propose_set_interface_ip`, `_propose_set_access_vlan` returns.
- [frontend/screen-ai.jsx:7-56](frontend/screen-ai.jsx:7) `synthesizeProposal` forwards `input.existing_entity` and `input.existing_block`.
- [frontend/screen-ai.jsx:385-442](frontend/screen-ai.jsx:385) `ProposalBubble` renders a "REPLACES EXISTING" block above the commands when `proposal.existing_entity` is present.
- New `.prop-existing-block` style in [frontend/styles.css](frontend/styles.css).

**Tag after chunks 6 + 7 + 8 land**: `v0.5.3-phase-c-conflict-detect` (Filip authorises).

## Chunk 10 — Anthropic 529 retry hardening (~20 min, MED) — Phase F carryover

Three changes:
1. `max_retries=5` on `Anthropic()` clients in [planner.py](backend/orchestration/planner.py), [configure_planner.py](backend/orchestration/configure_planner.py), [cli_configure_planner.py](backend/orchestration/cli_configure_planner.py).
2. Wrap `OverloadedError` → `{"error": "llm_overloaded", "message": "...", "request_id": exc.request_id}` in `_propose_*` + `_*_configure` in `tool_registry.py`.
3. Mock-tests: `messages.create` raises `OverloadedError`, assert friendly dict.

**Tag after landing**: `v0.5.4-overload-retry`.

## Remaining chunks (one-line each)

| # | Chunk | Phase | Est | Pri |
|---|---|---|---|---|
| 11 | Smart fast actions — `/api/suggestions` calls Haiku with running-config digest | D | ~60 min | MED |
| 12 | Auto-debug — reactive on write failure + on-demand sweep | G | ~90 min | MED |
| 13 | Config Preview cleanup — latest-action default, font fix, role decision | E | ~30 min | MED |
| 14 | WebUI speed pass — trim `_settle_page` waits, retest on live router | G | ~60 min | LOW |
| 14b | Self-training WebUI vision fallback — Claude Vision + learned selectors | G | ~4 h | MED |
| 15 | Hardware retests — ISIS + OSPF WebUI on live router after chunks 7 & 10 | F | ~30 min | MED |
| 16 | README + screenshots + GitHub metadata (manual — Filip drops 3 PNGs) | F | ~5 min | — |
| 17 | Cosmetic prototype-label sweep | F | ~10 min | LOW |
| 18 | Cut clean `v0.4.0-alpha.1` consolidation tag once 7, 10 land | F | ~15 min | — |

## Notes / housekeeping

- README.md modification still uncommitted in the working tree (waiting on the 3 PNGs from chunk 16).
- `frontend-design-backup/` sweep is its own commit later in the week (after Phase C feels stable).
- `tools/check_vectorstore.py` and `tools/query_rag.py` flagged in yesterday's dead-code audit as worth a follow-up review — not blocking.
- Director Blueprint applies from message #1 of the next chat. Use team voice, lead with tradeoffs on architectural decisions, route implementation through Sonnet and audits through Haiku.
- The plan file `~/.claude/plans/write-me-what-is-graceful-sparkle.md` is outside the repo; safe to delete after the next session starts since the roadmap and per-chunk briefings live in `docs/`.
