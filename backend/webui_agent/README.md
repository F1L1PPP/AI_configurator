# backend/webui_agent/ — module guide & v0.4.0 architecture decisions

## Purpose

This directory holds the WebUI configuration agent: a Playwright-driven
layer that changes Cisco IOS XE configuration through the router's
web UI. As of today it ships two working flows on real hardware
(hostname change, VLAN add) plus the supporting login / evidence /
verification / subprocess infrastructure.

The v0.4.0 plan (in flight — see
[../../docs/plan-ai-first-webui.md](../../docs/plan-ai-first-webui.md))
shifts new WebUI configuration work away from hand-coded Page Object
Models toward an AI-driven semantic-DOM driver. This README exists so
future contributors (human or AI) understand which modules are
load-bearing safety infrastructure that stays, which are kept as
cheap fast paths for high-frequency operations, and which areas
the new generic driver will replace.

## Keep / archive matrix

The rows below cover every module currently in this directory. The
"Decision" column reflects the v0.4.0 plan's Phase 2 review
([docs/plan-ai-first-webui.md §Phase 2](../../docs/plan-ai-first-webui.md)).
Phase 7 of the same plan will revisit "KEEP (for now)" entries after
the generic driver proves out on a real-router flow.

| Module / dir | Decision | Why |
| --- | --- | --- |
| `_subprocess.py` + `_playwright_subprocess.py` | **KEEP** | Load-bearing on Windows — see "Why subprocess isolation" below. Phase 3+ builds on top of this pattern; it must not be refactored back into the FastAPI process. |
| `browser.py` | **KEEP** | Playwright launch + viewport + `ignore_https_errors` for the router's self-signed cert. Configuration only; no replacement candidate. |
| `login.py` | **KEEP** | Router login is stable across IOS XE versions and uses a multi-strategy fallback (`get_by_label` / `get_by_role`). The new generic driver wraps everything *after* login; login itself stays as-is. |
| `selectors/` (incl. `iosxe_default.yaml`) | **KEEP** | Selector strategy YAML + Python loader for the existing flows. Entries not referenced by the remaining fast paths after v0.4.0 lands may be archived in Phase 7, never deleted. |
| `evidence.py` | **KEEP** | Screenshot + DOM dump on every WebUI step. Mandatory before any router write per [../../CLAUDE.md](../../CLAUDE.md). AI-driven flows in Phase 5 will reuse it unchanged. |
| `verify.py` | **KEEP** | CLI-side post-condition checks (`verify_hostname`, `verify_vlan`). The router CLI is the ground truth — every WebUI write is verified over SSH after the click. Reused as-is by the generic driver. |
| `flows/change_hostname.py` + `flows/add_access_vlan.py` | **KEEP as fast paths** | Shipped working end-to-end on real C1111 hardware (2026-05-12 and 2026-05-13). High-frequency, well-understood operations; cheaper to run a known-good script than an AI round trip with `describe_page`. |
| `pages/hostname_page.py` + `pages/vlan_page.py` | **KEEP (for now)** | Page Object Models backing the two fast paths above. If those fast paths are ever retired, archive these to `pages/_archive/` per Phase 7 rather than delete. |

Anything not listed above is either package boilerplate
(`__init__.py`, `.gitkeep`) or doesn't yet exist (`semantic_dom.py`,
`generic_driver.py`, `vision.py` arrive in Phases 3 / 4 / 6).

## Cross-package dependencies (also KEEP)

The flows in this directory call out to three siblings; all three are
load-bearing safety infrastructure and unchanged by v0.4.0:

- [`backend/cli_agent/snapshots.py`](../cli_agent/snapshots.py) — pre/post
  `show running-config` capture before every WebUI write, stored under
  `artifacts/device-snapshots/<action_id>/`.
- [`backend/orchestration/confirmations.py`](../orchestration/confirmations.py)
  — HITL state machine. Each flow checks `is_approved(action_id)`
  inside the function body (defense-in-depth layer 2; the dispatcher
  is layer 1).
- [`backend/orchestration/tool_registry.py`](../orchestration/tool_registry.py)
  — `_REQUIRES_APPROVAL` registers each write tool name. New
  AI-driven write tools (Phase 5) will register here too.

## Why subprocess isolation (the load-bearing pattern)

Playwright's sync API needs its own asyncio event loop; uvicorn
(the FastAPI server) already runs one. On Linux and macOS the two
loops compose cleanly. On Windows, the default `ProactorEventLoop`
policy creates a Catch-22: Playwright sync cannot nest inside it,
but switching to `SelectorEventLoop` breaks other Windows APIs we
rely on (the change was tried in commit `c0075ed` and reverted the
same day).

The fix that landed in commit `d4ce6a1` runs every Playwright
session in a fresh child Python process. The parent FastAPI server
spawns the child via [`_subprocess.py`](_subprocess.py); the child
runs the actual browser session in
[`_playwright_subprocess.py`](_playwright_subprocess.py). The
inter-process boundary is JSON over stdout/stdin. The child has
its own event loop, so the FastAPI loop in the parent never
collides with Playwright's.

Phase 3 onward (`semantic_dom.py`, `generic_driver.py`,
`vision.py`) will build *inside* the child process — `describe_page`
and `webui_act` run in the same Playwright session as the existing
flows. Do not refactor the subprocess split back into the main
process without re-validating the Windows Catch-22 from scratch on
a clean machine.

## Where to go next

- v0.4.0 phase-by-phase plan: [../../docs/plan-ai-first-webui.md](../../docs/plan-ai-first-webui.md)
- Cross-package architecture overview: [../../docs/how-it-works.md](../../docs/how-it-works.md)
- Workflow rules (branches, commits, snapshots, HITL): [../../CLAUDE.md](../../CLAUDE.md)
