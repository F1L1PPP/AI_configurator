# Next session kickoff — 2026-05-19+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. Read these five docs first, then summarise back in 6-8 sentences:

1. [docs/today-2026-05-18-evening-summary.md](docs/today-2026-05-18-evening-summary.md) — 2026-05-18 wrap (new frontend integration, v0.5.0 cut, CI fix, CLI write_tool bug surfaced)
2. [docs/today-2026-05-18-summary.md](docs/today-2026-05-18-summary.md) — 2026-05-18 morning wrap (settle-wait fix + alpha.4 tag + design handoff doc)
3. [docs/today-2026-05-15-evening-summary.md](docs/today-2026-05-15-evening-summary.md) — Phase 5 multi-propose chain + CLI configure + alpha.1/.2/.3 tags
4. [docs/plan-ai-first-webui.md](docs/plan-ai-first-webui.md) — v0.4.0 phase plan
5. [docs/how-it-works.md](docs/how-it-works.md) — current architecture walkthrough

After reading, summarise:

1. The state of `feature/bootstrap` at HEAD `9d166ac` — 523 tests passing, tag `v0.5.0-new-frontend` cut, new frontend serves at `http://localhost:8000/` via FastAPI StaticFiles
2. The CLI `set_interface_ip` silent-failure bug from 2026-05-18 — three issues stacked (hardware L2-only on C1111-4P + no output validation + empty proposal commands) — why this is TODAY'S FIRST CHUNK
3. The Anthropic 529 retry hardening that was originally planned for 2026-05-18 morning but got pre-empted by the frontend migration — still queued, moved later in the order
4. Two cosmetic gaps Filip flagged at end of day: WS origin allowlist missing `127.0.0.1:8000`, sidebar + Dashboard device-overview still reading mock data instead of `/api/devices`
5. The README rewrite + 3 screenshot placeholders + GitHub repo description text are sitting in the working tree uncommitted from yesterday — chunk 8 lands them once Filip drops the PNGs

Then wait for "go" before making any change. Don't propose re-planning — the chunk order below is locked unless Filip asks.

=== END ===

## Today's first chunk — CLI write_tool validation + verify (~45 min, HIGH)

**Why FIRST**: yesterday `set_interface_ip` on `Gi0/1/3` looked successful in the chat but the IP never landed on the router. The agent gave the operator false confidence in a no-op write. Until the write_tools verify their own work, every CLI write is suspect. See `docs/today-2026-05-18-evening-summary.md` "Bug surfaced" section for the full root-cause breakdown.

**Three sub-fixes**, all in [backend/cli_agent/write_tools.py](backend/cli_agent/write_tools.py):

1. **Output validation helper.** Add `_check_netmiko_output_for_errors(output: str) -> None`: scans for `% ` line prefixes (IOS XE's error marker) and raises `WriteRejectedError` listing the offending lines. Call it from every write_tool right after `conn.send_config_set(...)` and before the post-snapshot. ~15 lines.

2. **Post-write verify** per tool:
   - `set_hostname`: re-fetch `show running-config | include hostname` and assert `hostname <new_name>` appears
   - `set_interface_ip`: run `show running-config interface <name>` and assert `ip address <ip> <mask>` appears
   - `set_access_vlan`: run `show vlan brief` and assert the new VLAN row appears
   
   On verify failure: `mark_failed(action_id)` + raise. ~5 lines per tool.

3. **Docs**: append to [docs/router-prerequisites.md](docs/router-prerequisites.md) a note that the C1111-4P `Gi0/1/x` ports are hardware-L2-only and IPs go via the SVI pattern (`interface vlan N` + `switchport access vlan N` on the port). Saves the next operator the same diagnostic loop.

**Tests**: mock Netmiko's `send_config_set` to return a `% Invalid input` line; assert the write_tool raises `WriteRejectedError` instead of returning success. ~3 regression tests in `tests/unit/test_cli_write_tools.py`.

**Tag after landing**: `v0.5.1-write-validate`.

## Second chunk — Surface planned commands in fast-path proposals (~20 min, MED)

Fast-path proposals (`propose_set_hostname`, `propose_set_interface_ip`, `propose_set_access_vlan`) currently return only the structured params. The frontend's `synthesizeProposal` helper looks for `input.commands` and falls back to `[]` when absent — so the operator sees an empty `IOS XE commands` block before clicking Approve.

Extend each `propose_*` in [backend/orchestration/tool_registry.py](backend/orchestration/tool_registry.py) to include a `commands` field in its returned dict listing the CLI lines the corresponding write_tool will run. Frontend needs no change.

## Third chunk — WS origin allowlist: add 127.0.0.1 (~5 min, MED)

One-line fix in [backend/core/settings.py](backend/core/settings.py): add `"http://127.0.0.1:8000"` to the `allowed_origins` default list so navigating via `http://127.0.0.1:8000/` doesn't 403 the WS handshake.

## Fourth chunk — Sidebar + Dashboard device-overview wiring (~20 min, MED)

Two surfaces in `frontend/` still render mock data:

- Left sidebar `ACTIVE DEVICE` card — in `frontend/chrome.jsx` or `frontend/app.jsx` (grep `MOCK_DEVICES`)
- Dashboard `DEVICE OVERVIEW` panel — in `frontend/screens-basic.jsx` (grep `MOCK_DEVICES`)

Both should fetch from `window.api.fetchDevices()` on mount and use `devices[0]` as the active device (single-device project for now). Mirror the pattern from commit `f816288` (DevicesScreen + topbar count).

## Fifth chunk — Anthropic 529 retry hardening (~20 min, MED)

Carry-over from 2026-05-18 morning kickoff. Three changes:

1. Bump `max_retries=5` on `Anthropic()` clients in:
   - [backend/orchestration/planner.py](backend/orchestration/planner.py) (outer planner)
   - [backend/orchestration/configure_planner.py](backend/orchestration/configure_planner.py) (`draft_plan`)
   - [backend/orchestration/cli_configure_planner.py](backend/orchestration/cli_configure_planner.py) (`draft_cli_plan`)

2. Wrap `OverloadedError` → `{"error": "llm_overloaded", "message": "Anthropic API temporarily overloaded — retry in 1-2 minutes. Your action_id is preserved; clicking EXECUTE again will start fresh.", "request_id": exc.request_id}` in `_propose_webui_configure`, `_webui_configure`, `_propose_cli_configure`, `_cli_configure` in `tool_registry.py`.

3. Tests: mock `messages.create` to raise `OverloadedError`; assert friendly dict instead of `tool_failed`.

**Tag after landing**: `v0.5.2-overload-retry`.

## Sixth chunk — Hardware retests against the live router (~30 min, MED)

After chunks 1, 3, 5 are green:

- **ISIS WebUI**: re-run the `propose_webui_configure` flow against `/webui/#/isis` to verify alpha.4's `_settle_page` actually catches the modal race end-to-end (blocked yesterday by Anthropic 529s; chunk 5 unblocks).
- **OSPF WebUI**: same — alpha.3's click-Add fix only tested on the static-route page; retest OSPF specifically.

Smoke evidence to `artifacts/screenshots/...` per the usual convention.

## Seventh chunk — Router-id conflict pre-check (~45 min, MED)

Inner CLI planner ([backend/orchestration/cli_configure_planner.py](backend/orchestration/cli_configure_planner.py)) should scan running-config at propose time and refuse BGP/OSPF/EIGRP router-id conflicts BEFORE the operator approves, instead of letting the execute fail with a confusing post-hoc error.

## Eighth chunk — Land README + screenshots + GitHub metadata (~5 min, manual)

Filip's manual steps:

1. Save 3 PNGs from yesterday's design at:
   - `docs/screenshots/dashboard.png`
   - `docs/screenshots/ai-configuration.png`
   - `docs/screenshots/devices.png`
2. Commit + push the existing uncommitted README rewrite + the new PNGs.
3. Set the GitHub repo description + topics via web UI (text in `docs/today-2026-05-18-evening-summary.md` "README + GitHub repo polish" section).

## Ninth chunk — Cosmetic prototype-label sweep (~10 min, LOW)

Strip the "prototype" label from three remaining places that don't reflect the v0.5.0 state:

- `frontend/README.md` title: "Cisco AI Config — Frontend Prototype" → drop "Prototype"
- `frontend/index.html` `<title>`: "Cisco AI Config — Prototype" → drop "Prototype"
- `frontend/styles.css` header comment: "prototype styles" → "styles"

## Tenth chunk — Formal `v0.4.0-alpha.1` consolidation tag (~15 min)

Cut a clean `v0.4.0-alpha.1` (no suffix) once chunks 1, 5, 6, 7 land. Closes the original alpha-1 scope-lock from CLAUDE.md.

## Notes / housekeeping

- `frontend-design-backup/` can be swept once chunk 4 lands and the new frontend feels stable. Don't delete during this session; sweep is its own commit later in the week.
- The `tools/` directory (`check_vectorstore.py`, `query_rag.py`) was flagged in yesterday's dead-code audit as worth a follow-up review — not blocking, but a quick sweep if there's slack time.
- `~/.claude/plans/plan-how-we-would-transient-cook.md` (this session's plan file) is outside the repo; safe to delete after today's wrap commits.
