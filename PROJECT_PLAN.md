# Cisco AI Config Agent — Project Plan (v2)

**Repo:** https://github.com/F1L1PPP/AI_configurator
**Owner:** Filip
**Mode:** Solo, with Claude Code (Max plan)
**Original timeline:** 10 working days × 6h/day (parallel GUI track); compressed in reality — see §7.
**Target device:** Real Cisco C1111 (WebUI + console cable available)
**Source assignment:** `uploads/zadanie_projektu_cisco_ai_agent (1).docx`
**Status as of 2026-05-18:** rolling-alpha; current head is the `v0.4.0-alpha.4-pre-redesign` tag. Live state lives in [docs/next-session-kickoff.md](docs/next-session-kickoff.md) and [docs/today-2026-05-18-summary.md](docs/today-2026-05-18-summary.md) — this file is the design baseline, not the day-to-day journal.

> This plan is the result of comparing four independent AI-generated plans (Claude Sonnet 4.6, Gemini 3.1 Pro, ChatGPT 5.5 Deep Search, Perplexity) against the actual assignment. Section 11 lists where each plan was right, where it was wrong, and what I chose.

---

## 1. Goals & grading map (from assignment)

| Grading area | Weight | What unlocks the points |
|---|---|---|
| CLI agent (Project A) | 25 pts | SSH live demo: 3 `show` commands + hostname + interface IP, logging, HITL gate |
| RAG (Project B) | 20 pts | Doc-grounded query with source citation in response |
| WebUI agent | **30 pts** | Live demo: VLAN add + hostname change via Playwright, screenshots saved, verification |
| Integration & HITL | 15 pts | Full flow prompt → plan → approval → execute → verify, no autonomous writes |
| Code & docs | 10 pts | README + technical report (≥8 page PDF) + clean code |
| Bonus | +10 | Playwright MCP OR monitoring dashboard (the GUI is now a graded core deliverable per instructor, not optional — the dashboard inside it also lands the bonus) |

**Deliverables:** ZIP archive, technical report PDF (≥8 pages), 10–15 min demo (video or live).

---

## 2. The non-negotiable scope discipline

Two weeks is short. Most failures of this kind of project come from feature creep, not slow coding. The alpha must demo only these **six end-to-end scenarios** — nothing else, until alpha is frozen:

1. **CLI read** — show interfaces, show version, show running-config
2. **CLI write — hostname** — set hostname with preview + approval + verify + backup
3. **CLI write — interface IP** — set IP on Gi0/0/1 with preview + approval + verify + backup
4. **RAG query** — natural-language doc question returns chunks with source citation
5. **WebUI write — hostname** — Playwright clicks through WebUI, screenshots before/after, verify via CLI
6. **WebUI write — Access VLAN** — Playwright adds VLAN, screenshots, verify via CLI + WebUI list

Anything else (OSPF, ACLs, DHCP, static routes, monitoring dashboard polish) waits until after the `v0.4.0-alpha.1` freeze tag on Day 10. The GUI front-end is layered on top of this working alpha in Week 2 — not built before it.

---

## 3. Cisco C1111 pre-flight — do this on Day 1 before anything else

This is the single biggest gotcha from the ChatGPT plan: **the C1111 WebUI only shows Dashboard + Monitoring screens unless these prerequisites are met**. Verify all of them with the console cable on Day 1:

- [ ] User account with `privilege 15` exists (or create one)
- [ ] `ip http server` and `ip http secure-server` are enabled
- [ ] HTTPS auth works: `ip http authentication local` (or AAA)
- [ ] At least **30 VTY lines** are configured (`line vty 0 30`)
- [ ] SSHv2 is enabled: `ip ssh version 2`, `ip ssh time-out 60`
- [ ] Management IP is reachable from your dev machine (ping + SSH + HTTPS)
- [ ] You can manually log into the WebUI and reach **Configuration → VLANs** and **Administration → Device Properties**
- [ ] The WebUI version (visible in the top right) is recorded in `docs/router-prerequisites.md` — selectors are version-sensitive

If any of these fail, fix them on Day 1. Don't start Day 2 with a half-configured router.

---

## 4. Architecture (the decision)

### 4.1 The principle
**LLM plans. Python executes.** The model picks the tool, extracts parameters, drafts the click-path from RAG + semantic DOM grounding, and summarizes — but each individual click, command, and verification runs through deterministic Python functions with HITL approval per the propose/execute pattern. The full execution model is documented in [`docs/plan-ai-first-webui.md`](docs/plan-ai-first-webui.md). Pure autonomous browser agents — no approval gate, no propose step — remain out of scope.

### 4.2 Stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | 3.10 is security-only now; 3.12 is the safest current LTS-ish |
| LLM | **Anthropic Claude** (Opus 4.7 + Sonnet 4.6) | Opus for architecture/planning, Sonnet for code & tool-use loop |
| Agent framework | **Direct Anthropic SDK** (no LangChain) | Slimmer, easier to defend; reach for LangGraph only if state graphs become unwieldy |
| CLI transport | **Netmiko** | Standard, has session log + TextFSM/Genie hooks |
| CLI parsing | **TextFSM / Genie** (via Netmiko `use_textfsm=True`) | Structured output for reliable verification |
| WebUI automation | **Playwright (sync API + Pytest plugin)** | Auto-waiting locators, screenshots, traces, `ignore_https_errors=True` |
| Vector DB | **ChromaDB** (persistent) | Local, free, no server |
| Embeddings | **sentence-transformers** (`all-MiniLM-L6-v2`) | Local, free, fast enough; OpenAI/Voyage adds cost & key management |
| Config | **Pydantic Settings** + `.env` | Type-safe env loading |
| Linter/formatter | **Ruff** | One tool replaces several |
| Tests | **pytest** + `pytest-playwright` | Aligned with Playwright official guidance |
| API server | **FastAPI** | Async + WebSocket for live agent stream |
| Frontend | **Next.js 14 + TypeScript + Tailwind** | Matches the mockups; no SSR needed; static export possible |
| Realtime | **WebSocket** from FastAPI to frontend | Drives the "WebUI Agent Live" screen |
| DB (sessions/logs) | **SQLite** + SQLAlchemy | Zero-config |
| CI | **GitHub Actions** | Lint+test on push, nightly backup, release artifact bundle |

### 4.3 Hybrid execution model (per scenario)
1. User types: "Configure VLAN 30 named OFFICE on Gi0/0/1 in the WebUI"
2. **Outer planner** ([`backend/orchestration/planner.py`](backend/orchestration/planner.py), Haiku 4.5 tool-use loop) picks `propose_webui_configure(intent="…", webui_path="/webui/#/vlan")`
3. Backend starts a Playwright session (subprocess-isolated via [`backend/webui_agent/_subprocess.py`](backend/webui_agent/_subprocess.py)), navigates to `webui_path`, and calls `describe_page()` ([`semantic_dom.py`](backend/webui_agent/semantic_dom.py)) → token-bounded JSON snapshot of visible interactive elements + locator_map
4. **Inner Haiku** ([`configure_planner.draft_plan`](backend/orchestration/configure_planner.py)) drafts an intent-based step plan (e.g. `{action: "click", intent: "Add", value: null}`) grounded by RAG chunks + the `describe_page` view
5. Plan + risk note returned to frontend as an inline APPROVE control
6. User clicks **APPROVE** → `POST /api/approve/{action_id}` (atomic APPROVED → EXECUTING transition closes the TOCTOU window)
7. `POST /api/execute/{action_id}` runs each step via `webui_act_by_intent` ([`generic_driver.py`](backend/webui_agent/generic_driver.py)): resolve intent against current view, deterministic Python click, `_settle_page()` waits for Angular to stabilise (networkidle ≤1.5s + 500ms fallback), re-describe, repeat
8. **Verification**: CLI `show vlan brief` parsed for confirmation text (`verify.verify_vlan_exists`)
9. **Post-snapshot**: running-config + structured execution report saved to `artifacts/`
10. Result + sources surfaced in chat; event stream finalises with `verified`

### 4.4 Safety guarantees (server-enforced, not prompt-enforced)
- Write tools refuse to execute without a matching `action_id` that has been explicitly approved via the REST endpoint
- Every write triggers a pre-snapshot stored under `artifacts/device-snapshots/`
- Every WebUI step takes a screenshot saved under `artifacts/screenshots/<session>/`
- On any error the agent **stops and surfaces** — never auto-retries a write
- Secrets are loaded via Pydantic Settings from `.env`; `.env` is `.gitignore`d; `.env.example` is committed
- Netmiko session log uses the `no_log` filter to redact passwords
- Bricking guard: a known-good `running-config` is exported to USB **before** Day 3 starts

---

## 5. Repository layout

Reflects the actual tree as of 2026-05-18 (post AI-first shift). Some files listed in the original plan were removed during the alpha-1 chunks; others were added.

```
AI_configurator/
├── README.md
├── PROJECT_PLAN.md            ← this file
├── CLAUDE.md                  ← short, concrete rules for Claude Code
├── CLAUDE_INSTRUCTIONS.md     ← long-form companion to CLAUDE.md
├── pyproject.toml             ← ruff + pytest + project config
├── requirements.txt
│
├── .claude/
│   └── skills/
│       └── checkpoint/SKILL.md         ← /checkpoint: lint+test+commit+push+daily tag
│
├── .github/workflows/
│   ├── ci.yml                 ← ruff + pytest on push/PR
│   └── nightly-backup.yml     ← scheduled artifact upload
│
├── backend/
│   ├── main.py                ← FastAPI entrypoint
│   ├── core/
│   │   ├── settings.py        ← Pydantic Settings (loads .env)
│   │   ├── logging.py         ← structlog JSONL config
│   │   └── eventbus.py        ← async pub/sub for WS broadcasting
│   ├── api/
│   │   ├── routes_chat.py     ← POST /api/chat
│   │   ├── routes_approvals.py  ← POST /api/approve/{id}, /api/execute/{id}
│   │   ├── routes_ws.py       ← GET /ws/agent
│   │   └── routes_logs.py     ← GET /api/logs/recent
│   ├── orchestration/
│   │   ├── planner.py                  ← outer Haiku 4.5 tool-use loop
│   │   ├── tool_registry.py            ← Anthropic-format tool schemas + dispatch + approval gate
│   │   ├── confirmations.py            ← propose/approve/execute state machine (TOCTOU-safe)
│   │   ├── configure_planner.py        ← inner Haiku — WebUI step planning from describe_page
│   │   └── cli_configure_planner.py    ← inner Haiku — CLI command planning
│   ├── cli_agent/
│   │   ├── connection.py      ← Netmiko pool
│   │   ├── read_tools.py      ← show_* commands
│   │   ├── write_tools.py     ← set_hostname, set_interface_ip, set_access_vlan
│   │   ├── parsers.py         ← TextFSM/Genie wrappers
│   │   └── snapshots.py       ← pre/post running-config dumps
│   ├── webui_agent/
│   │   ├── browser.py                  ← Playwright launcher (ignore_https_errors=True)
│   │   ├── login.py
│   │   ├── _subprocess.py              ← subprocess-isolated Playwright session host
│   │   ├── _playwright_subprocess.py   ← worker process — `_settle_page` lives here
│   │   ├── semantic_dom.py             ← describe_page() — token-bounded JSON view + locator_map
│   │   ├── generic_driver.py           ← intent-resolution + per-step click loop + self-heal
│   │   ├── verify.py                   ← verify_hostname / verify_vlan_exists
│   │   ├── evidence.py                 ← screenshot + DOM dump on error
│   │   ├── selectors/iosxe_default.yaml  ← legacy fast-path selectors
│   │   ├── pages/                      ← legacy POM (still wired for fast paths)
│   │   │   ├── hostname_page.py
│   │   │   └── vlan_page.py
│   │   └── flows/                      ← legacy flow wrappers (still wired for fast paths)
│   │       ├── change_hostname.py
│   │       └── add_access_vlan.py
│   └── knowledge_agent/
│       ├── ingest.py          ← docs → chunks → ChromaDB
│       ├── chunking.py
│       └── retrieve.py        ← ChromaDB + sentence-transformers/all-MiniLM-L6-v2 (913 chunks)
│
├── frontend/                  ← Next.js 14 + TypeScript + Tailwind
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx           ← dashboard
│   │   ├── chat/page.tsx
│   │   ├── preview/page.tsx
│   │   ├── webui-live/page.tsx
│   │   └── actions/{page.tsx,change-hostname,set-interface-ip,add-vlan}
│   ├── components/
│   │   ├── LiveEventStream.tsx
│   │   ├── layout/{Sidebar,TopBar}.tsx
│   │   ├── dashboard/{ActionsCount,RecentActions}.tsx
│   │   ├── status/BackendStatus.tsx
│   │   ├── preview/ApprovalButtons.tsx
│   │   ├── actions/{ScenarioCard,ScenarioForm}.tsx
│   │   ├── webui-agent/{ActionTimeline,PhaseProgress}.tsx
│   │   └── mesh/{MeshSphere,EthernetLogo}.tsx
│   ├── lib/{api,ws,errors}.ts
│   └── tailwind.config.ts
│
├── knowledge_base/
│   ├── docs/                  ← raw Cisco PDFs (C1100 HIG, WebUI guide, ISR1100 SW config)
│   ├── vectorstore/           ← ChromaDB persistent (gitignored)
│   └── webui-catalog/
│       └── current.json       ← recorder snapshot of WebUI nav + page elements
│
├── artifacts/                 ← runtime, gitignored
│   ├── screenshots/<session>/
│   ├── cli-logs/
│   └── device-snapshots/
│
├── logs/                      ← gitignored — structlog JSONL
├── backups/                   ← gitignored — local rollback configs
├── playwright_playground/     ← exploratory scripts (kept for reference)
│
├── scripts/
│   ├── checkpoint.sh
│   ├── checkpoint.ps1
│   ├── run_smoke_tests.py
│   ├── catalog_webui_elements.py    ← static catalog walker
│   └── record_webui_catalog.py      ← interactive recorder for WebUI nav map
│
├── tests/
│   ├── unit/                  ← ~50 modules; planner, configure_planner, semantic_dom, …
│   ├── integration/           ← routes_execute, ws_agent
│   └── smoke/scenarios/       ← 6 end-to-end scenarios from §2
│
└── docs/
    ├── plan-ai-first-webui.md       ← v0.4.0 phase plan (load-bearing)
    ├── next-session-kickoff.md      ← live state pointer
    ├── today-2026-05-*.md           ← daily journals
    ├── design-handoff.md            ← parallel-track redesign brief
    ├── router-prerequisites.md
    ├── smoke-scenarios.md
    ├── how-it-works.md
    ├── clean-config-walkthrough.md
    └── technical_report.md    ← source for the PDF deliverable
```

---

## 6. Versioning & branch strategy

### 6.1 Branches
| Branch | Purpose |
|---|---|
| `main` | Always stable; tagged releases only |
| `develop` | Active integration |
| `feature/*` | Each unit of work (e.g. `feature/cli-write-hostname`) |
| `release/alpha-1-freeze` | Immutable branch cut from `v0.4.0-alpha.1` — the safe-rollback hard floor |
| `hotfix/*` | Stability fixes against `main` or `release/alpha-1-freeze` |

### 6.2 Tag sequence
| Tag | Created on | What it contains |
|---|---|---|
| `v0.0.1-bootstrap` | 2026-05-11 | Repo skeleton, CI, Pydantic settings, logging (idempotent + stderr-safe), GUI foundation, Playwright training ground, prerequisites verified on real Cisco C1111. |
| `v0.1.0-cli-core` | 2026-05-12 | CLI read + safe write + HITL + pre/post snapshots + restore round-trip + WebUI cert/login probe + Chat/Preview GUI skeletons. |
| `v0.2.0-agent-core` | 2026-05-13 | Tool registry + RAG + Sources display + WebSocket events wired to Chat. Outer Haiku 4.5 tool-use loop already shipped 2026-05-12. |
| `v0.3.0-webui-core` | 2026-05-13 | Playwright login + hand-coded hostname flow + VLAN flow + screenshots + verify + GUI polish. |
| `v0.3.1-audit-fixes` … `v0.3.6-security-review` | 2026-05-13 → 14 | Pre-pivot hardening — audit fixes, POM stabilisation, AI-first foundations laid, catalog recorder shipped, security review. |
| `v0.4.0-alpha.1` | *to be cut* | Formal milestone — to be cut once chunks 2-4 of next session land (ISIS + OSPF hardware retest + router-id pre-check). See [docs/next-session-kickoff.md](docs/next-session-kickoff.md). |
| `v0.4.0-alpha.1-ai-configure` | 2026-05-15 | First hardware-validated AI-first cut: multi-propose chain + generic CLI configure + spatial-label fix + CIDR splitting + null-verify loop continuation. |
| `v0.4.0-alpha.2-retry-guard` | 2026-05-15 | Per-turn propose quota + OSPF `\| section` → `\| include` parser fix. |
| `v0.4.0-alpha.3-add-button` | 2026-05-15 | Inner planner clicks Add when intent says add; `device_errors` field surfaces `%` lines from Cisco. |
| `v0.4.0-alpha.4-settle-wait` | 2026-05-18 | `_settle_page()` between every action and re-describe — survives Cisco's Angular auto-dismiss modal race (ISIS class of bug). |
| `v0.4.0-alpha.4-pre-redesign` | 2026-05-18 | Freeze marker before the frontend redesign track starts. 521 tests green; backend stable. |

#### Future tags
- Next session will add `v0.4.0-alpha.5-overload-retry` for Anthropic 529 hardening, then cut the formal un-suffixed `v0.4.0-alpha.1` once chunks 2-4 are hardware-validated.
- The original `v0.5.0-rc.1` and `v1.0.0-demo` placeholders are deferred — the rolling-alpha approach has replaced the fixed Day 9/10 cadence. Demo cut + submission tag will land once the frontend redesign + tech report are in.

The frontend GUI is built **in parallel** with the backend — instructor requires it as a core graded deliverable, not bonus. At the formal alpha milestone all six §2 scenarios must pass end-to-end AND the GUI must consume the real WebSocket event stream.

### 6.3 Mechanical backup rhythm (not "by feel")
- Every 60–90 min OR after each logical task → commit to feature branch
- After each green lint/test → push to remote
- End of each day → annotated tag `backup-YYYYMMDD-HHMM` + GH Actions artifact upload
- After each milestone → GitHub Release with attached ZIP
- **Before every router write** → device snapshot to `artifacts/device-snapshots/`
- **After every router write** → post-snapshot + verification evidence
- AI never moves tags or modifies the `release/alpha-1-freeze` branch — this is a hard rule in `CLAUDE.md`

### 6.4 Evidence captured on every write
1. `show running-config` (pre + post)
2. `show version`
3. `show ip interface brief`
4. Redacted Netmiko session log
5. WebUI screenshot pre-change
6. WebUI screenshot post-change
7. Playwright trace (on error) or screenshot+DOM dump
8. Structured execution report (prompt, tool, params, result)
9. Diff (or semantic summary) of running-config

---

## 7. Milestone summary (the original 10-day plan, compressed + pivoted)

The original §7 was a Day 1–10 schedule (2026-05-11 to ~2026-05-20). Reality compressed it into a few calendar days, then a major architecture shift (AI-first WebUI) replaced the back half of the schedule with a rolling-alpha track. The day-by-day receipts live in [docs/today-*.md](docs/) journals; this section keeps only the milestone shape.

### Days 1–5 (2026-05-11 → 2026-05-12) — DONE

- **Day 1 — Bootstrap + router pre-flight.** Repo skeleton, CI, Pydantic Settings, structlog, FastAPI healthz, GUI scaffolds, C1111 prerequisites verified. Tag: `v0.0.1-bootstrap`.
- **Day 2 — CLI read.** Netmiko pool + `show_*` tools + ntc-templates parsers + JSONL action log; Dashboard wired to real `/api/logs/recent`.
- **Day 3 — CLI write + HITL + WebUI probe.** `set_hostname` / `set_interface_ip` gated on `is_approved`; propose/approve/execute state machine; full hostname round-trip on the real C1111. Tag: `v0.1.0-cli-core`.
- **Day 4 — Orchestrator + WebUI discovery.** Outer Haiku 4.5 tool-use loop, SK/EN prompt, 8 Anthropic-format tool schemas with two-layer approval gate; Playwright browser/login/evidence scaffolds.
- **Day 5 — WebUI hostname flow + verify.** Hand-coded POM + flow, `verify_hostname()`, SSH-pool-invalidation fix for stale prompts. End-to-end WebUI hostname round-trip proven on the real router in 23 s on 2026-05-12.

### Days 6–7 — RAG + WebSocket + hand-coded WebUI flows (DONE 2026-05-13)

- RAG: heading-aware chunking, sentence-transformers/all-MiniLM-L6-v2 embeddings, ChromaDB persisted; `search_docs` tool with source + section. 913 chunks from the C1100 HIG, WebUI guide, and ISR1100 SW config.
- WebSocket: `core/eventbus.py` + `GET /ws/agent`; planner publishes `agent_thinking` / `tool_call` / `awaiting_approval` / `applied` / `verified` events; frontend `lib/ws.ts` consumes them.
- WebUI VLAN flow: hand-coded POM + flow with screenshots and CLI cross-verify.
- Smoke harness: all 6 §2 scenarios runnable via `scripts/run_smoke_tests.py`.
- Tags: `v0.2.0-agent-core`, `v0.3.0-webui-core`.

### Day 8 — original `v0.4.0-alpha.1` cut + AI-first decision (DONE 2026-05-14)

- Pre-pivot hardening tags landed: `v0.3.1-audit-fixes` → `v0.3.6-security-review`, including POM stabilisation, AI-first foundations, and the WebUI element recorder.
- **The pivot:** hand-coded Playwright POMs scaled poorly to every new Cisco WebUI page. Decision recorded in [docs/plan-ai-first-webui.md](docs/plan-ai-first-webui.md): replace flow-per-feature with a generic intent path — inner Haiku drafts steps from `describe_page()` snapshots; deterministic Python clicks and waits.
- New modules introduced: `semantic_dom.describe_page()`, `generic_driver.webui_act_by_intent()`, `_subprocess.py` + `_playwright_subprocess.py`, `configure_planner.draft_plan()`, `cli_configure_planner.draft_plan()`. Legacy POMs + flows stay in the repo for fast-path features.

### Alpha-1 chunks (2026-05-14 → 2026-05-18)

Iterative tags after the AI-first pivot, each fixing a specific class of bug surfaced by hardware testing:

| Tag | Date | Fix |
|---|---|---|
| `v0.4.0-alpha.1-ai-configure` | 2026-05-15 | First hardware-validated cut: multi-propose chain + generic CLI configure + spatial-label fix + CIDR splitting + null-verify loop continuation. |
| `v0.4.0-alpha.2-retry-guard` | 2026-05-15 | Per-turn propose quota; OSPF `\| section` → `\| include` parser fix. |
| `v0.4.0-alpha.3-add-button` | 2026-05-15 | Inner planner clicks Add when intent says add; `device_errors` surfaces Cisco `%` lines. |
| `v0.4.0-alpha.4-settle-wait` | 2026-05-18 | `_settle_page()` (networkidle ≤1.5 s + 500 ms fallback) between every action and re-describe; survives Cisco Angular auto-dismiss modal race (ISIS class of bug). |

### Pre-redesign freeze (2026-05-18)

Tag `v0.4.0-alpha.4-pre-redesign` marks the current state: 521 tests green, backend stable, frontend redesign about to start as a parallel track. Designer has [docs/design-handoff.md](docs/design-handoff.md); no mockups yet.

### Next

Per [docs/next-session-kickoff.md](docs/next-session-kickoff.md), the next session covers in order:
1. **Anthropic 529 retry hardening** — bump `max_retries=5` at every `Anthropic()` client; wrap `OverloadedError` in `_webui_configure` / `_cli_configure` and the matching propose tools. Tag `v0.4.0-alpha.5-overload-retry`.
2. **ISIS WebUI hardware retest** — confirm `_settle_page` actually keeps the Add modal open long enough for `describe_page` to capture the form.
3. **OSPF WebUI hardware retest** — alpha.3's click-Add rule validated on hardware for OSPF specifically.
4. **router-id conflict pre-check** — refuse at propose time when the operator picks a router-id already in use (inner planner pre-flight grep over running-config).
5. **Consolidate to `v0.4.0-alpha.1`** — formal milestone tag, no suffix, once chunks 1–4 are validated on the C1111.

Demo cut + tech report + submission ZIP are deferred until after the frontend redesign lands.

---

## 8. Definition of done

A day is "done" only when:
1. All listed deliverables work against the real C1111
2. New code passes ruff + pytest
3. Smoke loop for that day's scenario runs without intervention
4. Action log + artifacts captured
5. README updated for any new install step
6. Commit pushed; tag applied if it's a milestone day
7. I can demo it in under 2 minutes with no notes

"Bug-free for 2 weeks" means:
- Zero open P0/P1 in the frozen scope (the 6 scenarios)
- Each smoke scenario passes 5× in a row on the real router
- Rollback to `release/alpha-1-freeze` is one `git checkout` away — proven

---

## 9. `CLAUDE.md` — short rules for the coding agent

`CLAUDE.md` will be created on Day 1. ChatGPT's research point applies: keep it **short and concrete**. Long instruction files reduce adherence. The full enforcement lives in `.claude/skills/*` (slash-commands) and shell scripts. Planned contents:

- Always work on a `feature/*` branch. Never commit directly to `main`.
- Never touch tags or `release/alpha-1-freeze`.
- Before every write tool against the router: take a device snapshot first.
- Every write requires server-side approval via `/api/approve/{action_id}` — there is no prompt-only override.
- Run `ruff check` + relevant tests before every commit.
- Commit messages follow Conventional Commits (`feat(cli-agent): ...`, `fix(webui): ...`).
- After 60–90 min of work, push the feature branch.
- At end of day, run `/checkpoint` (lint + test + commit + push + daily tag + artifact upload).
- Never commit `.env`, real credentials, `artifacts/`, `logs/`, `vectorstore/`, `screenshots/`, `__pycache__/`, `node_modules/`, `.venv/`.
- If a Playwright flow fails: save screenshot + DOM dump + trace, **do not auto-retry write operations**, surface to me for manual decision.
- Use Opus 4.7 for: architecture, planning, hard bug diagnosis, release gate reviews.
- Use Sonnet 4.6 for: bulk implementation, tests, refactors, page analysis.

---

## 10. Risk register

| Risk | P | Impact | Mitigation |
|---|---|---|---|
| WebUI prerequisites missing → only Dashboard visible | High | Critical | §3 pre-flight checklist verified 2026-05-12 on real C1111 (Configuration + Administration menus visible to `cisco` priv-15 user). |
| WebUI selectors break across IOS XE versions | Medium | High | Multiple selector strategies per element; `selectors/iosxe_default.yaml` from codegen capture on Day 4; codegen rerun if router firmware upgrades. Day 9 is buffer if breakage surfaces late. |
| Self-signed cert blocks Playwright | High | Medium | `ignore_https_errors=True` + `--ignore-certificate-errors` Chromium arg; **validated on script 05 against the real router 2026-05-12**. |
| Cisco WebUI session timeout (5 min idle) | High | Medium | Relogin detection + session keepalive helper live in `webui_agent/login.py` (Day 4); flow tests exercise it on Day 5 hostname flow. |
| RAG retrieves irrelevant chunks → bad CLI | Medium | Medium | Curate doc set; 10-query relevance eval on Day 5; confidence threshold |
| Orchestrator approves its own writes | Low | Critical | Server-enforced approval gate (not prompt) |
| 10 days tight for backend + parallel GUI | High | High | Backend on §2 critical path is non-negotiable; GUI is 1.5h/day strictly capped — if backend slips on a day, that day's GUI work is dropped, not vice versa. Mesh decoration / device-connection page treated as Day 8 polish, only if there's spare time. |
| Anthropic rate limits during demo | Low | Medium | Cache last good plan; pre-recorded demo video as fallback |
| Bricking the router with bad config | Low | Critical | Known-good config exported to USB Day 1; mandatory pre-snapshot |
| AI-assisted dev sprawls into unmaintainable diffs | Medium | High | Mechanical 4h/1h/1h day blocks; small feature branches; `/checkpoint` skill |
| Playwright MCP / browser-use complexity blows the timeline | Medium | Medium | **Discovery use only**; deterministic flows are the production path |

---

## 11. What I took from each AI plan

| Plan | Best contributions I kept | Where I disagreed |
|---|---|---|
| **ChatGPT 5.5 (Deep Search)** | C1111 WebUI prerequisites (privilege 15 / 30 VTY / http server); hybrid agent principle; 4-block daily structure; full versioning sequence with `release/alpha-1-freeze`; `.claude/skills/` for repeatable workflows; mechanical backup rhythm; Pydantic Settings; Ruff; pytest-playwright; pyATS optional; Python 3.12 | Adopted almost everything — it was the most rigorous plan |
| **Claude Sonnet 4.6** | Page Object Model under `webui_agent/pages/`; Jinja2 templates as future enhancement; `networkidle` wait helper; 5-min Cisco WebUI session timeout warning; fallback to Netmiko on 3 WebUI failures; CLAUDE.md commit-frequency rule | Skipped Anthropic computer-use beta for v1 — too risky for 14 days; deterministic flows ship first |
| **Gemini 3.1 Pro** | Use direct Anthropic SDK (skip LangChain); per-milestone tag → push rule; Opus-for-planning + Sonnet-for-coding model split; CLAUDE.md template | Disagreed on swapping local sentence-transformers for paid OpenAI/Voyage embeddings — local is fine for a school project and avoids extra keys |
| **Perplexity** | Branch model (`main`/`develop`/`feature/*`/`hotfix/*`); "AI never updates tags or backup branches" safety rule; YAML config templates | Their schedule was too sparse — adopted ChatGPT's day-by-day instead |

---

## 12. Day 1 kickoff checklist

Before any code, gather:
- [ ] Router management IP
- [ ] SSH username + password (privilege 15)
- [ ] WebUI username + password (privilege 15)
- [ ] Anthropic API key
- [ ] Console cable connected and tested
- [ ] Known-good `running-config` exported to USB
- [ ] §3 pre-flight verified
- [ ] Confirmation that you want me to proceed

All secrets go into the local `.env` file you create. Never paste them in chat.

When all of the above is true, say **"start Day 1"** and I'll begin.

---

## 13. Working rules between us

- I do not start a day's work until we agree on its scope
- If reality forces a deviation from this plan, we update §7 here first, then code
- I summarize at the end of every day (what shipped, what's open, what's next)
- Major decisions (e.g., changing the stack) are surfaced as questions, not assumed
