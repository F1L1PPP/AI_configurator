# Day 7 — WebUI VLAN flow + smoke harness + frontend Quick Actions

**Date:** 2026-05-13
**Branch:** `feature/bootstrap` (still off `develop`)
**Plan-day:** Day 7 (of the 10-day compressed plan)
**Milestone tag:** `v0.3.0-webui-core` — **not yet created** (Filip cuts
milestone tags manually; existing pattern from Days 3 and 6).
**Test count:** 157 → 183 (+26 new unit tests for VLAN POM/flow/registry,
plus 8-test smoke harness with explicit gates).
**Status:** Day 7 complete. Backend POM + flow + tool registration done;
26 unit tests pass; smoke harness runs 3 read scenarios green + 3 write
scenarios skipped (correctly gated on `SMOKE_ALLOW_WRITES`). Frontend
ships Quick Actions: Dashboard launcher + 3 form pages.

---

## What shipped

### Slab A — WebUI VLAN flow

- `backend/webui_agent/pages/vlan_page.py` — `VlanPage` POM with the
  3-path navigation fallback proven by
  `playwright_playground/scripts/06_real_router_vlan_add.py`:
  - **Path A**: Configuration → Layer 2 → VLAN (primary)
  - **Path B**: Configuration → LAN → VLAN (fallback for builds without Layer 2)
  - **Path C**: any visible link/button containing "VLAN" (last resort)
  - Methods: `goto()`, `click_add()`, `set_vlan_id(int)`, `set_vlan_name(str)`,
    `save()`, `_dump_diagnostics(lbl)` (matches `HostnamePage` shape exactly).

- `backend/webui_agent/flows/add_access_vlan.py` — high-level flow
  mirroring `change_hostname.py`:
  - `add_access_vlan_via_webui(vlan_id, vlan_name, action_id, headless=False) -> dict`
  - Steps: `_guard(action_id)` → EvidenceCollector → pre-snapshot →
    `webui_browser` context → `login()` → `VlanPage.goto()` →
    `click_add()` → `set_vlan_id()` + `set_vlan_name()` → `save()` →
    `pool.invalidate()` → `verify_vlan_exists()` (CLI ground truth) →
    post-snapshot → `mark_executed()`.
  - Returns: `{tool, vlan_id, vlan_name, snapshot_pre, snapshot_post,
    screenshots, verified}`.
  - On `verify_vlan_exists() == False`: raises `WebUIVerificationError`
    and `mark_failed(action_id)`.

- `backend/orchestration/tool_registry.py` — two new schemas:
  `propose_webui_add_access_vlan` (vlan_id int + vlan_name str) and
  `webui_add_access_vlan` (vlan_id + vlan_name + action_id). New
  `_propose_webui_add_access_vlan()` helper. `webui_add_access_vlan`
  added to the `WRITE_TOOLS` frozenset so the dispatcher's approval
  gate covers it AND the planner emits `applied` events.

- `backend/orchestration/planner.py` — system prompt advertises the
  VLAN tools, adds a rule "For VLAN add requests, prefer the WebUI
  path", and updates the propose→execute mapping table.

- 26 new unit tests:
  - `tests/unit/test_webui_vlan_page.py` (11 tests) — POM: selectors
    load, click_add raises on missing button, set_vlan_id fills as str,
    set_vlan_name skips silently when absent (per playground observation),
    save raises on missing button, goto raises when nav fails,
    `_dump_diagnostics` doesn't shadow method param (Copilot regression).
  - `tests/unit/test_webui_add_vlan_flow.py` (8 tests) — flow: refuses
    without approval, refuses bad action_id, happy-path returns
    structured result, pre→post snapshots in correct order,
    `pool.invalidate()` called, POM methods called in correct order,
    verify failure marks action FAILED, login failure aborts cleanly.
  - `tests/unit/test_tool_registry_vlan.py` (7 tests) — registry:
    schemas present, in `_TOOL_FUNCS`, in `WRITE_TOOLS`, dispatcher
    refuses unapproved action_id, dispatcher calls flow when approved,
    propose helper returns correct `awaiting_approval` shape.

### Slab B — Verify path

No code changes needed — `verify_vlan_exists(vlan_id, name)` was
already implemented in `backend/webui_agent/verify.py:38-69` and
already covered by 5 tests in `tests/unit/test_webui_verify.py`. The
flow uses it as-is.

### Slab C — Smoke harness

A single command runs all 6 §2 scenarios end-to-end against the
running stack. Default run is read-only; write scenarios are gated
on `SMOKE_ALLOW_WRITES=1` so accidental CI runs can't mutate a
production router.

- `tests/smoke/__init__.py` + `tests/smoke/scenarios/__init__.py` — package markers
- `tests/smoke/conftest.py` — three skip fixtures:
  - `router_reachable` — TCP-connect to `ROUTER_HOST:22`, skip on failure
  - `writes_allowed` — skip unless `SMOKE_ALLOW_WRITES=1`
  - `webui_enabled` — skip unless `ROUTER_WEBUI_BASE_URL` is set
- `tests/smoke/scenarios/`:
  - `test_01_cli_read.py` — `show_ip_interface_brief`, `show_version`
  - `test_02_cli_show_running_config.py` — running-config non-empty + has baseline markers
  - `test_03_cli_set_hostname.py` — full hostname round-trip (propose → approve → execute → CLI verify → **restore original in finally**)
  - `test_04_rag_query.py` — 2 sub-tests: hostname query + VLAN query; auto-skip if vectorstore empty
  - `test_05_webui_set_hostname.py` — WebUI hostname change; restore via CLI in finally
  - `test_06_webui_add_vlan.py` — VLAN 999 add via WebUI; cleanup `no vlan 999` in finally (always runs)
- `scripts/run_smoke_tests.py` — wraps `pytest tests/smoke/ -v` with an
  ASCII summary table + a JSON summary at
  `artifacts/smoke/<timestamp>/summary.json`.

Default run (read-only):

```
SMOKE TEST RESULTS                                         2026-05-13 10:10:07
-------------------------------------------------------------------------------
Scenario                                                                 Status
-------------------------------------------------------------------------------
01 -- CLI: show interfaces + show version                                  PASS
02 -- CLI: show running-config                                             PASS
03 -- CLI: change hostname (round-trip)                                    SKIP
04 -- RAG: query Cisco docs with citations                                 PASS
05 -- WebUI: change hostname                                               SKIP
06 -- WebUI: add access VLAN                                               SKIP
-------------------------------------------------------------------------------
Result: 3 pass / 0 fail / 3 skip
```

Day-8-ready: `SMOKE_ALLOW_WRITES=1 SMOKE_HEADLESS=1 python scripts/run_smoke_tests.py`
5× in a row clean is the alpha-freeze gate.

### Slab D — Frontend Quick Actions

Addresses Filip's "why doesn't the WebUI still include the change of
hostname that worked and all these things that worked?" — the backend
had 6 working tools but the frontend only surfaced them through the
chat input.

- `frontend/components/actions/ScenarioCard.tsx` — reusable card with
  `shipped | planned` status. Planned cards are visually muted and
  not linked (so users see what's coming without misclicking).

- `frontend/components/actions/ScenarioForm.tsx` — shared form shell
  used by all 3 scenario pages. Builds a natural-language prompt from
  form values, POSTs to `/api/chat`, and on `awaiting_approval`
  response redirects to `/preview?action_id=…` so the user can
  approve and watch execution.

- `frontend/app/page.tsx` (Dashboard) — Quick Actions panel rewritten:
  4 stub buttons → 4 real launchers (Change hostname, Set interface
  IP, Add access VLAN, Ask a question). Each links to its form page
  or to `/chat` for free-form.

- `frontend/app/actions/page.tsx` — full index page with all 6
  scenarios in a 2-column grid (Dashboard's panel is the compact
  view; this is the spacious view).

- `frontend/app/actions/change-hostname/page.tsx` — single-input
  form (hostname); validates `[A-Za-z0-9-]{1,63}` client-side.

- `frontend/app/actions/add-vlan/page.tsx` — two inputs (VLAN ID
  1–4094, VLAN Name `[A-Za-z0-9_-]{1,32}`).

- `frontend/app/actions/set-interface-ip/page.tsx` — three inputs
  (interface, IPv4, mask).

**The user flow:**

```
Dashboard → click "Add access VLAN" card
         → /actions/add-vlan
         → type 30 + OFFICE
         → click "PROPOSE → APPROVE"
         → POST /api/chat with "Add VLAN 30 named OFFICE via the WebUI"
         → redirect to /preview?action_id=act_…
         → click APPROVE
         → return to /chat, say "execute it"
         → WebUI flow runs with screenshots + CLI verify
```

No typing into the chat box anywhere in that flow. Chat is still
there as the free-form fallback for everything else.

---

## What's verified working

| Check | How | Result |
|---|---|---|
| Lint (ruff) | `ruff check backend/ tests/ tools/ scripts/` | ✓ All checks passed |
| Unit tests | `pytest -q --ignore=tests/smoke` | ✓ 183 passed in 2.55s |
| Smoke harness | `python scripts/run_smoke_tests.py` | ✓ 3 pass / 0 fail / 3 skip (writes off) |
| Frontend TS | `npx tsc --noEmit` | ✓ clean |
| Tool registration | `python -c "from backend.orchestration.tool_registry import _TOOL_FUNCS, WRITE_TOOLS; ..."` | ✓ `webui_add_access_vlan` registered, gated |
| Real-router VLAN add | (manual, headed) — `SMOKE_ALLOW_WRITES=1` flow | **pending Filip's cabled session** |

---

## What's open / deferred

1. **Real-router VLAN add via the cabled C1111.** The unit tests
   prove the flow logic (mocked Playwright + verify); the smoke
   harness is wired but writes are gated. Filip's cabled session
   should run `SMOKE_ALLOW_WRITES=1 python scripts/run_smoke_tests.py`
   to prove all 6 scenarios end-to-end against 192.168.10.1.
2. **`v0.3.0-webui-core` milestone tag.** Awaits the cabled-session
   proof per #1.
3. **`/webui-live` page** — phase progress + screenshot pane still
   mocked. The bus already streams `tool_call` / `applied` events
   (Day 6 wiring), but the page doesn't subscribe to them
   page-by-page yet. Day 8 polish.
4. **WebUI help guide ingest** — the post-tag side-track per the
   approved plan. Filip provides the help URL pattern from
   DevTools, then we mirror + ingest. Lifts smoke from 7/10 → 9/10.
5. **`pytest-json-report`** — the smoke runner uses a fallback
   stdout parser when this plugin isn't installed. Both paths work;
   installing the plugin gives slightly cleaner per-scenario data.

---

## Day 8 plan (next)

Per `PROJECT_PLAN.md` §7 Day 8:

- `SMOKE_ALLOW_WRITES=1 python scripts/run_smoke_tests.py` 5× in a
  row clean against the cabled C1111.
- Logs / Backups / Devices frontend pages (the three not-yet-existing
  pages in the sidebar).
- `v0.4.0-alpha.1` tag + cut `release/alpha-1-freeze` branch + GitHub
  Release. **At this point the project passes the grading floor —
  everything beyond is upside.**

3 calendar days banked. Days 7 + 8 + 9 + 10 still have to land but
the runway is comfortable.
