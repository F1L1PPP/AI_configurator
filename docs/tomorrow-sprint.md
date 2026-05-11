# Tomorrow's Sprint — Day 2 + Day 3 in one calendar day

> **Goal:** close Day 1 cabled prereqs, ship Day 2 (CLI read layer + Dashboard
> wired to logs), ship Day 3 (CLI write + HITL + WebUI cert/login probe) — all
> tomorrow. Doable because the planning, repo skeleton, GUI screens, and
> Playwright training are all already done. ~12 h of focused work.
>
> If you only have 6–7 h, the natural cut is end of Phase 1 (Day 2 done); Day
> 3 slips one day and you're still on schedule.

---

## Phase 0 — Cabled prereqs (≈45 min)

**Kickoff prompt:** `"resume Day 1 — router pre-flight"`

Walk [`docs/router-prerequisites.md`](router-prerequisites.md) end-to-end with
the router at the console. Nine numbered steps already laid out — verify
priv-15 user / `ip http server` / 30 VTY / SSHv2 / reachability / WebUI walk
(record version) / USB known-good export / throwaway SSH+HTTPS probe.

**Output:**
- Filled-in checklist committed (no secrets pasted)
- `.env` populated with real `ROUTER_HOST` / SSH user / SSH password / WebUI
  user / WebUI password / WebUI base URL
- USB drive with `known-good-YYYYMMDD.cfg` physically separated from the router
- `v0.0.1-bootstrap` tag either **moved forward** (`git tag -f`) onto the new
  filled-in commit, or **supplemented** with `v0.0.1.1-bootstrap-prereqs`
  (you decide — both work)

---

## Phase 1 — Day 2: CLI read + Dashboard logs wire (≈5.5 h)

**Kickoff prompt:** `"start Day 2"`

### Backend (≈3.5 h)

| File | Purpose |
|---|---|
| `requirements.txt` (edit) | Add `netmiko==4.4.x` + `ntc-templates` |
| `backend/cli_agent/__init__.py` | (empty package marker) |
| `backend/cli_agent/connection.py` | Netmiko connection pool — one persistent SSH session per device, context-managed, handles first-run host key, retries only on connect (never on a write) |
| `backend/cli_agent/parsers.py` | Wrap Netmiko `use_textfsm=True`; fall back to regex when TextFSM has no template for the `show` command |
| `backend/cli_agent/read_tools.py` | 4 read functions: `show_version()`, `show_ip_interface_brief()`, `show_running_config()`, `show_vlan_brief()` — each calls connection + parser, returns typed dict / list of dicts |
| Action logger | Already in `backend/core/logging.py` — every tool call writes one JSONL line to `logs/actions.log` with `tool`, `params`, `result_summary`, `duration_ms` |
| `tests/unit/test_cli_connection.py` | Mocked Netmiko, asserts pool reuse + connect-retry only |
| `tests/unit/test_cli_parsers.py` | Canned `show` output → assert parsed dict shape |
| `tests/unit/test_cli_read_tools.py` | Mocked connection → assert each tool returns expected shape |

**Smoke against real router (≈15 min):** for each `show_*` tool, run once,
print the parsed output, eyeball the structure, save the raw response into
`artifacts/cli-logs/day2-smoke/` for reference.

### GUI (≈1.5 h)

| What | File |
|---|---|
| `GET /api/logs/recent?limit=20` endpoint | new `backend/api/routes_logs.py` — reads last N lines from `logs/actions.log`, parses each JSONL line, returns as JSON array |
| Wire router from `backend/main.py` | `app.include_router(routes_logs.router)` |
| Dashboard "Recent Activity" panel — new `<RecentActions>` client component | `frontend/components/dashboard/RecentActions.tsx` — polls `/api/logs/recent` every 3 s, renders the 4 most recent rows replacing the current mock data |
| Update `frontend/app/page.tsx` | import `<RecentActions />` and use it in the Recent Activity panel slot |

### Tests + commit (≈0.5 h)

Run `ruff check` + `pytest -q` (target: 10–12 tests passing). `npm run build`.
`/checkpoint "feat(cli-agent): ship Day 2 read layer + dashboard logs wire"`.

---

## Phase 2 — Day 3: CLI write + HITL + WebUI probe (≈6 h)

**Kickoff prompt:** `"start Day 3"`

### Backend — write tools + snapshots (≈2 h)

| File | Purpose |
|---|---|
| `backend/cli_agent/write_tools.py` | `set_hostname(new_name)`, `set_interface_ip(interface, ip, mask)`. Each: requires approved `action_id`, captures pre-snapshot, sends config commands via Netmiko, captures post-snapshot, returns structured result |
| `backend/cli_agent/snapshots.py` | `take_snapshot(session_id)` — runs `show running-config` + `show version` + `show ip int brief` in one SSH session, saves all three to `artifacts/device-snapshots/<session>/{pre,post}/` |
| `backend/services/restore.py` | `restore_config(snapshot_path)` — uploads the pre-snapshot back via SCP / `copy startup-config running-config` semantics. **Rollback path only — never auto-invoked.** |

### Backend — HITL approval gate (≈1.5 h)

| File | Purpose |
|---|---|
| `backend/orchestration/__init__.py` | (empty) |
| `backend/orchestration/confirmations.py` | `ActionState` enum (`PROPOSED`, `APPROVED`, `REJECTED`, `EXECUTED`, `VERIFIED`, `FAILED`), `propose_action()`, `approve_action(action_id)`, `is_approved(action_id)`. In-memory dict for Day 3; persisted to SQLite Day 12. |
| `backend/api/routes_approvals.py` | `POST /api/approve/{action_id}`, `GET /api/actions/{action_id}` |
| Write tools refuse if not approved | `if not is_approved(action_id): raise NotApproved` |

### Backend — smoke + tests (≈1 h)

Smoke: change hostname on real router → verify → restore from snapshot.
Tests: mocked Netmiko + mocked approval state, asserts write refuses without
approval, asserts pre-snapshot fires before any config command.

### WebUI cert/login probe (≈30 min)

Per the revised `PROJECT_PLAN.md §7 Day 3`, exploratory only — no production
code. Run:

```powershell
python -m playwright codegen https://<router-ip>
```

Walk Login → Configuration → Layer 2 → VLAN → Add VLAN → Save (don't actually
save — back out before submit). Save the recorded Python script to
`playwright_playground/draft_real_router_codegen.py` (gitignored — local input
for Day 4). Confirm:

- Login form submit lands on Dashboard with no redirect loop
- `ignore_https_errors=True` on the context handles the C1111's self-signed
  cert (no certificate warning page)
- The actual `<input>` / `<button>` / `<select>` element shapes are roughly
  what `playwright_playground/site/vlan-add.html` mocked

### GUI — Preview screen wired (≈45 min)

The `/preview` page already exists (mocked since today). Tomorrow:
- Replace the mocked APPROVE button with a real `POST /api/approve/{action_id}`
  fetch in a new client component `frontend/components/preview/ApprovalButtons.tsx`
- On success, show the verified state; on failure, show the error toast

### Tests + commit + tag (≈0.5 h)

Run all checks. `/checkpoint` with the seconds-precision tag. **You** create
the milestone tag:

```powershell
git tag -a v0.1.0-cli-core -m "CLI read + safe write + HITL + snapshots + WebUI cert/login probe"
git push origin v0.1.0-cli-core
```

---

## Definition of done for tomorrow

Phase 0 (always required):
- ☐ All 9 prereq boxes checked in `docs/router-prerequisites.md`
- ☐ `.env` populated, USB stored separate, `v0.0.1-bootstrap` tag now reflects verified prereqs

Phase 1 (Day 2):
- ☐ 4 wrapped `show_*` tools working against real C1111
- ☐ Each smoke-tested, raw output captured in `artifacts/cli-logs/day2-smoke/`
- ☐ Dashboard "Recent Activity" shows real entries that appear when you manually trigger a `show` command via curl / Python REPL
- ☐ All unit + 1 smoke test green

Phase 2 (Day 3):
- ☐ `set_hostname` + `set_interface_ip` work against real router via approved action_id
- ☐ Refuses without approval (test asserts this)
- ☐ Pre + post snapshots saved under `artifacts/device-snapshots/<session>/`
- ☐ `restore_config` round-trip proven (change hostname → restore → hostname back)
- ☐ Preview screen APPROVE button does a real POST and reflects the result
- ☐ Playwright `codegen` recording for real router VLAN add saved locally
- ☐ `v0.1.0-cli-core` tag created and pushed

---

## Cut points (if running short)

| Stop after | What you have | What slips |
|---|---|---|
| Phase 0 only | Prereqs done, `.env` filled, bootstrap tag verified | Day 2 to day-after-tomorrow. Still ahead of 10-day schedule. |
| Phase 1 (Day 2 only) | CLI read working + GUI shows live logs | Day 3 to day-after. Still on schedule (would have been today anyway). |
| Phase 2 (full Day 3) | CLI write + HITL + WebUI probe = **ahead by 1 day** on the 10-day plan | Nothing — banked a buffer day |

---

## Risks I expect tomorrow

| Risk | Mitigation |
|---|---|
| **WebUI shows only Dashboard / Monitoring after login** (the `§10` risk register entry) | Re-check priv-15 user; most common cause is the user missing privilege 15 even though everything else looks right. Re-login. |
| **Netmiko first-connect prompts for SSH host key** and hangs | Pre-accept on dev box: `ssh -o StrictHostKeyChecking=accept-new <user>@<ip> exit` before the first Netmiko call |
| **TextFSM template missing** for one of the four `show` commands on your exact IOS XE version | Parsers.py falls back to regex by design. If a parser returns raw string instead of dict, swap in a custom template under `tests/fixtures/textfsm/` |
| **`show running-config` is very large** (slow + Netmiko buffer) | `Netmiko.send_command(..., read_timeout=60)` + use `expect_string` |
| **Cert handling probe shows a warning page anyway** | The C1111's cert might be wildly old; pass `chromium_args=["--ignore-certificate-errors"]` in addition to `ignore_https_errors=True` |
| **You're tired by Phase 2** | Take the cut point. Day 3 tomorrow afternoon is fine; Day 3 the day after is also fine. The schedule absorbs it. |

---

## What's in your "go bag" for tomorrow

| Item | Why |
|---|---|
| Cisco C1111 + power adapter | The target |
| **Ethernet patch cable** | Management connection between dev box and router |
| **Console (rollover) cable + USB adapter** | Initial CLI before SSH is verified |
| **USB drive (FAT32, ≥ 64 MB)** | Known-good `running-config` bricking guard |
| Dev laptop with `.venv` set up | Already done today |
| Playwright + Chromium installed | Already done today |
| `docs/router-prerequisites.md` open in editor | The fill-in checklist |
| `docs/tomorrow-sprint.md` (this file) | The phase-by-phase plan |
| `docs/rag-sources.md` open | Day 7 prep — only consult if Phase 2 finishes fast and you want bonus work |
