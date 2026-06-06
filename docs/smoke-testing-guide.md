# Smoke Testing Guide

How to smoke-test the Cisco AI Config Agent against the **live C1111-4P** router. Smoke
testing is the real gate for any CLI/router or WebUI-driver change — unit tests run
mocked; smoke proves it works against the actual device.

There are **two modes**:

| Mode | What it is | When to use |
|---|---|---|
| **A — Automated harness** | The 6 alpha scenarios as pytest, auto-approve + rollback | Regression: confirm nothing broke before a tag/merge |
| **B — Live interactive loop** | Launch the app, drive it from chat, watch logs | New sections / driver work (static route, VLAN, ACL, Kendo, FEAT-SMART) |

> **Discipline:** for any session involving a live smoke, invoke the **`live-smoke-iteration`**
> skill first. The five rules in §4 are load-bearing — they were paid for in a 6-hour saga.

---

## 0. Prerequisites (both modes)

- **Work from the worktree** (the repo root `backend/` has no source):
  `C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5`
- **Reuse the main-checkout venv** (the worktree has none): `C:\GIT\AI_configurator\.venv\Scripts\python.exe`
- **Live router reachable** — SSH on `:22` and WebUI on `:443`.
- **`.env` present in the worktree** (copy of `.env.example`, real values, never committed):
  `ANTHROPIC_API_KEY`, `ROUTER_HOST`, `ROUTER_SSH_USER`/`PASSWORD`,
  `ROUTER_WEBUI_USER`/`PASSWORD`, `ROUTER_WEBUI_BASE_URL`.
- Quick reachability check (PowerShell):
  ```powershell
  Test-NetConnection 192.168.x.x -Port 22    # SSH
  Test-NetConnection 192.168.x.x -Port 443   # WebUI
  ```

A handy alias for the rest of this guide:
```powershell
$PY = "C:\GIT\AI_configurator\.venv\Scripts\python.exe"
# run everything from the worktree:
cd C:\GIT\AI_configurator\.claude\worktrees\loving-villani-1fe4d5
```

---

## 1. Pre-smoke gate (always run first)

Smoke is expensive (real router, ~minutes). Never smoke red code. Gate locally first:

```powershell
& $PY -m pytest tests/unit -q          # unit suite (must be green)
& $PY -m ruff check backend tests
& $PY -m ruff format --check .
& $PY -m mypy
```

> ⚠️ **Do NOT pipe pytest through `tail`/`head`** — the pipeline's exit code is the
> filter's, not pytest's, which hides collection errors. Run pytest unpiped (or check
> the printed `N passed` line). Lesson from 2026-06-06.

---

## 2. Mode A — the automated smoke harness

The 6 scenarios that define the alpha freeze. They **auto-skip** when prerequisites
aren't met, so the same suite is safe in CI (no router), on a laptop (router up), and
against a live cabled box (`SMOKE_ALLOW_WRITES=1`).

```powershell
# Read-only scenarios only (1, 2, 4) — safe, never mutates the router:
& $PY scripts\run_smoke_tests.py

# Full set incl. write scenarios — MUTATES then restores the router:
$env:SMOKE_ALLOW_WRITES = "1"; & $PY scripts\run_smoke_tests.py

# WebUI scenarios headless (CI-style):
$env:SMOKE_HEADLESS = "1"; $env:SMOKE_ALLOW_WRITES = "1"; & $PY scripts\run_smoke_tests.py

# Or drive pytest directly:
& $PY -m pytest tests\smoke\ -v
```

**Skip matrix** (from `tests/smoke/conftest.py`):
- `router_reachable` — skips if SSH to `ROUTER_HOST:22` refuses to connect
- `writes_allowed` — skips write scenarios unless `SMOKE_ALLOW_WRITES=1`
- `webui_enabled` — skips WebUI scenarios unless `ROUTER_WEBUI_BASE_URL` is set

**Exit code:** `0` = every scenario that *ran* passed (skips don't fail); `1` = a failure.
**Output:** an ASCII summary table + `artifacts/smoke/<timestamp>/summary.json`.

**The 6 scenarios:**

| # | Scenario | Write? | Verifies |
|---|---|---|---|
| 1 | CLI read (show int/version/run) | no | parsed payloads, ≥3 interfaces |
| 2 | CLI write hostname | yes | `show run \| i hostname` contains new name |
| 3 | CLI write interface IP | yes | `show ip int brief` row up + correct IP |
| 4 | RAG query | no | reply has `Sources:` + cites `knowledge_base/docs/` |
| 5 | WebUI change hostname | yes | WebUI writes, **CLI** confirms (cross-tool) |
| 6 | WebUI add access VLAN | yes | CLI `show vlan brief` + WebUI list row both show it |

Write scenarios auto-approve via the HITL gate and **roll back** at the end (re-apply the
original value) so the router ends as it started. Only run writes against a router you own.

---

## 3. Mode B — the live interactive loop (the real driver gate)

This is how OSPF + DHCP were validated and how you smoke a **new** section (static route,
VLAN, ACL, interfaces) or driver change (Kendo write path, FEAT-SMART).

### 3.1 Launch the app

PowerShell, **from the worktree**, with the `ANTHROPIC_API_KEY` shadow workaround (a shell
hook injects an empty key that shadows `.env` → fail-fast cred check trips on boot):

```powershell
Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
$env:ANTHROPIC_API_KEY = (Select-String -Path .env -Pattern '^ANTHROPIC_API_KEY=(.*)$').Matches.Groups[1].Value
& $PY -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000/** — one uvicorn process serves the API (`/api/*`, `/ws/agent`)
and the frontend SPA on the same origin. The WebUI driver opens a **headed** Chromium on
execute so you can watch the clicks.

### 3.2 Watch the log (visibility-first)

In a second terminal, tail the structured log and keep an eye on the uvicorn stdout:
```powershell
Get-Content logs\actions.log -Wait -Tail 20
```
Surface and read these event types — they are the evidence:
`vision_fallback_*`, `selector_cache_evicted`, `plan_vision_check_*`,
`webui_configure_atlas_*`, the `action_id`, and any IOS `%` error line.

### 3.3 Drive one scenario

1. Type the intent in chat, e.g. *"Using the WebUI, add a static route 10.50.0.0/24 via 192.168.10.254."*
2. The agent proposes → **Approve** → **Execute now** (headed Chromium opens).
3. Watch: the perceive→plan→fill→Apply→verify→snapshot pipeline; the screenshots; the log events.
4. Confirm the change landed (the agent self-verifies; cross-check via a CLI `show` or the router WebUI).
5. Note the `action_id` — all evidence is keyed by it.

### 3.4 Collect evidence (one smoke, one artifact)

Per `action_id`, under `artifacts/`:
- `device-snapshots/<action_id>/{pre,post}.cfg` — running-config before/after
- `screenshots/<action_id>/*.png` — every WebUI step
- `reports/<session>.json` — prompt, tool, params, result, timings
- `traces/<session>.zip` — Playwright trace (on failure)

---

## 4. The five load-bearing rules (`live-smoke-iteration`)

1. **Visibility-first.** Two consecutive smokes fail with the same generic symptom
   (`unknown_error`, `iteration_cap_hit`, silence)? **Stop shipping features and ship the
   observability fix first.** You cannot fix what you cannot see.
2. **One smoke, one piece of evidence.** Name the failure mode before changing anything —
   the exact event, selector, `%` line, or `action_id`. "It failed" is not evidence.
3. **Wiring-trap prevention.** For every new function / strategy / YAML, **grep for runtime
   callers and count call sites before commit.** Dead code that passes tests is still dead
   and still burns a smoke.
4. **Backup-tag discipline.** Tag `backup-YYYYMMDD-HHMM` mid-day and end-of-day on a
   live-iteration day. Informational, never moved, never a release tag — your revert net.
5. **Deep-audit, no skipping.** Never skip the Opus 4.8 deep audit on a smoke-touching
   chunk, no matter how small the surface looks.

---

## 5. The loop: ship → smoke → triage

```
   pre-smoke gate (pytest/ruff/mypy)  →  live smoke  →  PASS → backup-tag → next scenario
                                              │
                                              └─ FAIL → name failure mode + evidence
                                                        → root cause → page-agnostic fix
                                                        → re-gate → re-smoke
```

- Fixes must be **general** (capture/adapter/planner policy), never per-section hardcoding.
- When the Director paste-bombs a terminal log, that **is** a smoke result: parse it for the
  `action_id`, resolved selector, which events fired, and the IOS `%` line; lead with the
  named failure mode → one-line root cause → fix. Don't restate the whole log.

---

## 6. What to smoke next (current state)

OSPF + DHCP already configure end-to-end through the generic atlas engine. Open items
(see `docs/smoke-findings-20260605.md` and `docs/next-session-kickoff.md`):
- **Prove breadth** — static routes, VLAN, ACL, interfaces through the same engine.
- **Kendo WRITE path** — setting a non-default dropdown value (today only idempotent-skip works).
- **FEAT-SMART** — Advanced-tab discovery + capability-aware clarification.
- **CLI bugs F1–F5** — esp. **F2** (`set_interface_ip` prepends `no switchport`, breaking
  routed Gi0/0/x WAN ports).

---

## 7. Common gotchas

- **`ANTHROPIC_API_KEY` ValidationError on boot** → the empty-key shell shadow. Use the
  launch workaround in §3.1 (`Remove-Item Env:\ANTHROPIC_API_KEY` then inject from `.env`).
- **`networkidle` timeouts** → expected on the Cisco WebUI; the atlas path uses explicit
  visibility waits instead. Not a failure by itself.
- **Routed vs switched ports** → Gi0/0/0 and Gi0/0/1 are L2-incapable WAN ports; `no
  switchport` throws `% Invalid input` on them (bug F2).
- **`uvicorn: command not found` / `Error loading ASGI app`** → run via the venv python
  (`& $PY -m uvicorn …`) from the worktree.
- **Router left dirty after a failed write** → the action is FAILED but the line may have
  landed (IOS applies line-by-line). Check `device-snapshots/<action_id>/post.cfg` and
  restore manually if needed.

---

## 8. Reference

- `docs/smoke-scenarios.md` — the 6 scenarios in full (prompts, expected tools, assertions).
- `docs/smoke-findings-20260605.md` — the live fix-backlog (F1–F5, FEAT-SMART, Kendo).
- `docs/next-session-kickoff.md` — current state + first actions.
- `.claude/skills/live-smoke-iteration/SKILL.md` — the discipline + vision-stack lessons.
- `CLAUDE.md` "Before every router write" — snapshot → approval → screenshots → never auto-retry.
