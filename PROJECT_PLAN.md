# Cisco AI Config Agent — Project Plan (v2)

**Repo:** https://github.com/F1L1PPP/AI_configurator
**Owner:** Filip
**Mode:** Solo, with Claude Code (Max plan)
**Timeline:** 10 working days × 6h/day = 60h (parallel GUI track)
**Target device:** Real Cisco C1111 (WebUI + console cable available)
**Source assignment:** `uploads/zadanie_projektu_cisco_ai_agent (1).docx`

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
**LLM plans. Python executes.** The model picks the tool, extracts parameters, and summarizes — but the actual clicks, commands, and verifications run as deterministic Python functions. Pure autonomous browser agents are too risky for a 14-day project.

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
1. User types: "Configure VLAN 30 named OFFICE on Gi0/0/1"
2. **Orchestrator (Claude tool-use)** picks: `webui_add_access_vlan(id=30, name="OFFICE", interface="Gi0/0/1")`
3. **Knowledge agent** retrieves doc chunks for context, attaches to the plan as evidence
4. **Pre-flight snapshot** runs: `show running-config`, `show ip interface brief`, screenshot of current VLAN list
5. **Preview** is broadcast over the WebSocket → GUI shows planned actions + risk level + affected resource
6. User clicks **APPROVE** in the GUI → POST `/api/approve/{action_id}`
7. **WebUI agent** (Playwright) runs the deterministic flow with auto-waiting locators, screenshots between clicks
8. **Verification**: CLI agent runs `show vlan brief`, parses with TextFSM, confirms VLAN 30 exists
9. **Post-snapshot**: screenshot + show running-config + diff + structured report saved to `artifacts/`
10. Result + sources surfaced in the GUI; event stream finalizes with `verified`

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

```
AI_configurator/
├── README.md
├── PROJECT_PLAN.md            ← this file
├── CLAUDE.md                  ← short, concrete rules for Claude Code
├── .env.example
├── .gitignore
├── pyproject.toml             ← ruff + pytest + project config
├── requirements.txt
│
├── .claude/                   ← project-scoped Claude Code skills
│   ├── settings.json
│   └── skills/
│       ├── checkpoint/SKILL.md         ← /checkpoint: lint+test+commit+push+daily tag
│       ├── release-alpha/SKILL.md      ← /release-alpha: tag v0.x + freeze branch + GH release
│       └── bugbash/SKILL.md            ← /bugbash: run 5x smoke loop, report failures
│
├── .github/workflows/
│   ├── ci.yml                 ← ruff + pytest on push/PR
│   ├── nightly-backup.yml     ← tag backup-YYYYMMDD on schedule, upload artifacts
│   └── release-artifacts.yml  ← on tag: build ZIP, attach to GitHub Release
│
├── backend/
│   ├── main.py                ← FastAPI entrypoint
│   ├── core/
│   │   ├── settings.py        ← Pydantic Settings (loads .env)
│   │   ├── logging.py         ← structlog JSONL config
│   │   ├── models.py          ← Pydantic data models (Plan, Action, Snapshot, …)
│   │   ├── redaction.py       ← strip secrets from logs
│   │   └── eventbus.py        ← async pub/sub for WS broadcasting
│   ├── api/
│   │   ├── routes_chat.py     ← POST /chat, WS /ws/agent
│   │   ├── routes_devices.py
│   │   ├── routes_approvals.py
│   │   ├── routes_sessions.py
│   │   └── routes_backups.py
│   ├── orchestration/
│   │   ├── planner.py         ← Claude tool-use loop
│   │   ├── tool_registry.py   ← Anthropic-format tool schemas
│   │   ├── confirmations.py   ← approval gate state machine
│   │   └── execution_report.py
│   ├── cli_agent/
│   │   ├── connection.py      ← Netmiko pool
│   │   ├── read_tools.py      ← show_* commands
│   │   ├── write_tools.py     ← set_hostname, set_interface_ip
│   │   ├── parsers.py         ← TextFSM/Genie wrappers
│   │   ├── snapshots.py       ← pre/post running-config dumps
│   │   └── verify.py
│   ├── webui_agent/
│   │   ├── browser.py         ← Playwright launcher (ignore_https_errors=True)
│   │   ├── login.py
│   │   ├── selectors/
│   │   │   └── iosxe_default.yaml  ← versioned selector maps
│   │   ├── pages/             ← Page Object Model per WebUI section
│   │   │   ├── dashboard_page.py
│   │   │   ├── hostname_page.py
│   │   │   └── vlan_page.py
│   │   ├── flows/             ← high-level user flows
│   │   │   ├── change_hostname.py
│   │   │   └── add_access_vlan.py
│   │   ├── evidence.py        ← screenshot + DOM dump on error
│   │   └── verify.py
│   ├── knowledge_agent/
│   │   ├── ingest.py          ← docs → chunks → ChromaDB
│   │   ├── chunking.py
│   │   ├── retrieve.py
│   │   └── citations.py
│   ├── services/
│   │   ├── backup.py
│   │   └── screenshot.py
│   └── db/
│       ├── models.py          ← SQLAlchemy
│       └── migrations/
│
├── frontend/                  ← Next.js 14 + TypeScript + Tailwind
│   ├── app/(routes)/...
│   ├── components/
│   │   ├── layout/{Sidebar,TopBar}.tsx
│   │   ├── mesh/{MeshSphere,EthernetLogo}.tsx   ← decorative SVG mesh per design spec
│   │   ├── chat/MessageStream.tsx
│   │   ├── agent/ActionTimeline.tsx
│   │   ├── preview/ConfigDiff.tsx
│   │   └── webui-agent/BrowserFrame.tsx
│   ├── lib/{api,ws}.ts
│   ├── styles/globals.css     ← Tailwind + design tokens (black/white/gray + JetBrains Mono)
│   └── tailwind.config.ts
│
├── knowledge_base/
│   ├── docs/                  ← raw Cisco PDF/HTML (gitignored — large)
│   └── vectorstore/           ← ChromaDB persistent (gitignored)
│
├── artifacts/                 ← runtime, gitignored
│   ├── screenshots/<session>/
│   ├── traces/                ← Playwright traces
│   ├── cli-logs/              ← Netmiko session logs (redacted)
│   ├── device-snapshots/      ← pre/post running-config + show outputs
│   └── reports/               ← per-execution structured reports
│
├── logs/                      ← gitignored
│   └── actions.log            ← structlog JSONL
│
├── backups/                   ← gitignored — local rollback configs
│
├── scripts/
│   ├── checkpoint.sh          ← invoked by /checkpoint skill
│   ├── checkpoint.ps1
│   ├── export_device_snapshot.py
│   ├── seed_vectorstore.py
│   ├── run_smoke_tests.py
│   └── create_release_bundle.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── smoke/                 ← 6 end-to-end scenarios from §2
│   └── fixtures/
│
└── docs/
    ├── architecture.md
    ├── backup-policy.md
    ├── router-prerequisites.md
    ├── selector-map.md        ← every WebUI selector + how to re-derive it
    ├── smoke-scenarios.md     ← the 6 scenarios from §2
    ├── release-checklist.md
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
| `v0.0.1-bootstrap` | Day 1 | Repo skeleton, CI, Pydantic settings, logging, GUI foundation (design tokens + Sidebar + Dashboard + Chat + Preview + WebUI Live screens), Playwright training ground, Copilot review fixes round 1+2. **NB:** initial tag created end of Day 1 (local-only); prerequisites-verified portion lands the next day via either `git tag -f v0.0.1-bootstrap` after the cabled session or a supplementary `v0.0.1.1-bootstrap-prereqs` tag — operator's choice. |
| `v0.1.0-cli-core` | Day 3 | CLI read + safe write + HITL + pre/post snapshots + WebUI cert/login probe + Chat/Preview GUI skeletons |
| `v0.2.0-agent-core` | Day 7 | Orchestrator + tool registry + RAG minimum + real-time WebSocket GUI (Chat + Action Timeline + Agent Live) |
| `v0.3.0-webui-core` | Day 8 | Playwright login + hostname flow + VLAN flow + screenshots + verify + GUI polish |
| `v0.4.0-alpha.1` | **Day 9** | **First fully working end-to-end build → cut `release/alpha-1-freeze` immediately** |
| `v1.0.0-demo` | Day 10 | Final submission tag (tech report PDF + demo video + submission ZIP) |

The frontend GUI is built **in parallel** with the backend, ~2h/day from Day 1 onwards — instructor requires it as a core graded deliverable, not bonus. If a given day's backend work overruns, the GUI work for that day is dropped (not vice versa). At alpha freeze (Day 9), all six §2 scenarios pass end-to-end AND the GUI consumes the real WebSocket event stream.

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

## 7. Day-by-day plan (10 working days × 6h, parallel GUI track)

Each day allocates roughly **3.5h backend + 1.5h GUI + 1h test/commit/push**. The split is flexible (some days are more backend-heavy, some more GUI), but the rule is: **backend on the §2 critical path comes first; if backend slips on a given day, that day's GUI work is dropped, not vice versa.**

**Day 1 — Bootstrap + GUI foundation + router pre-flight**
- Backend: repo skeleton, `.gitignore`, `.env.example`, `pyproject.toml` (ruff+pytest), `requirements.txt`, Pydantic Settings, structlog, FastAPI healthz, CI workflows, checkpoint skill, unit tests ✓ (shipped)
- GUI: design tokens (Tailwind: black/white/gray + Inter + JetBrains Mono), `Sidebar`, `TopBar`, app-level layout, Dashboard page with mocked "Recent Actions" / "Device Status" / "Live Agent" cards, browser-tab title
- (Deferred — cabled session) §3 router pre-flight checklist + USB known-good config export
- **Tag (after pre-flight):** `v0.0.1-bootstrap`

**Day 2 — CLI read + Dashboard wired**
- Backend: `cli_agent/connection.py` (Netmiko pool, 1 persistent SSH session), `read_tools.py` (`show_version`, `show_ip_interface_brief`, `show_running_config`, `show_vlan_brief`), `parsers.py` (Netmiko `use_textfsm=True` with regex fallback), action logger JSONL with redaction
- GUI: Dashboard wired to real `/healthz` (live status indicator), recent-actions list reads from `logs/actions.log` (still mocked structure, real data from Day 6)
- Smoke: call each `show_*` against real router, validate parsed structure
- **Commit + push**

**Day 3 — CLI write + HITL + WebUI probe + Chat/Preview skeletons**
- Backend: `cli_agent/write_tools.py` (`set_hostname`, `set_interface_ip`), `snapshots.py` (pre/post), `orchestration/confirmations.py` (action_id state machine, server-enforced approval gate), `restore_config(path)` rollback helper
- Backend (30 min): WebUI cert/login probe — Playwright headed against router, confirm login flow + cert bypass, `playwright codegen` capture rough selectors as Day 4 input
- GUI: Chat page skeleton (mocked conversation), Preview screen skeleton (mocked plan + Approve/Reject buttons)
- Smoke: change hostname on real router, verify, restore from backup
- **Tag:** `v0.1.0-cli-core`

**Day 4 — WebUI discovery + Agent Live skeleton**
- Backend: Playwright codegen on real router (Login → Configuration → VLANs → Add VLAN; Administration → Device Properties), fill `webui_agent/selectors/iosxe_default.yaml` with multiple strategies per element (role/text/css), `browser.py` (headed dev, `ignore_https_errors=True`, viewport pinned), `login.py` (login flow class + session keepalive + relogin detection), `wait_for_load_state("networkidle")` helper
- GUI: **WebUI Agent Live screen skeleton** — placeholder screenshots from `public/`, action timeline component skeleton (mocked steps)
- **Commit + push + Playwright trace as artifact**

**Day 5 — WebUI hostname flow + Agent Live wired**
- Backend: `pages/hostname_page.py` (POM) + `flows/change_hostname.py`, screenshots before/after each step into `artifacts/screenshots/<session>/<step>.png`, verify via CLI (`show running-config | i hostname`), error handling (screenshot + DOM dump + abort, no auto-retry)
- GUI: WebUI Agent Live screen reads real screenshots from `artifacts/screenshots/<session>/` via a `/api/screenshots/{session}` endpoint (polled every 500ms — no WebSocket yet)
- Smoke: change hostname via WebUI on real router, verify via CLI, restore from backup
- **Commit + push**

**Day 6 — Orchestrator + WebSocket + real-time GUI**
- Backend: `orchestration/planner.py` (direct Anthropic SDK tool-use loop, Sonnet 4.6), tool schemas in Anthropic format, register CLI + WebUI hostname tools, WebSocket broadcast (`agent_thinking`, `tool_call`, `tool_result`, `awaiting_approval`, `applied`, `verified`, `error`), refusal path
- GUI: `lib/ws.ts` WebSocket client, Chat page consumes message events, Action Timeline consumes tool-call events, WebUI Agent Live replaces polling with WS-pushed screenshot events
- Smoke: "show interfaces" → CLI agent → reply visible in Chat; "change hostname via WebUI" → preview in Preview screen → approve via Preview → applied → verified, all events visible in GUI in real time
- **Commit + push**

**Day 7 — RAG + Sources display**
- Backend: `knowledge_agent/ingest.py` (curated ~50–100 MB C1111 + IOS XE 17.x guides scoped to VLANs/hostname/WebUI nav/interfaces), heading-aware chunking ~500 tok with 50-tok overlap, embeddings via `all-MiniLM-L6-v2` persisted to ChromaDB, `retrieve.search_docs(query, top_k=5)` returning chunks with source + section, orchestrator prompt updated to call `search_docs` before unfamiliar configs
- GUI: Sources display on Chat replies — citation badges (file + page/heading), clickable to expand chunk preview
- Smoke: 10 test queries hand-graded for relevance (≥7/10 passes)
- **Tag:** `v0.2.0-agent-core`

**Day 8 — WebUI VLAN flow + smoke harness + GUI polish**
- Backend: `pages/vlan_page.py` (POM) + `flows/add_access_vlan.py`, screenshots, verify VLAN appears via WebUI list + CLI `show vlan brief`, wire into orchestrator (`webui_add_access_vlan`); `tests/smoke/` — all 6 §2 scenarios as runnable scripts; `scripts/run_smoke_tests.py` runs all of them
- GUI: bug fixes + polish (loading states, error toasts, empty states, mesh sphere decoration if time permits)
- **Tag:** `v0.3.0-webui-core`

**Day 9 — Alpha freeze + demo b-roll**
- Backend: `scripts/run_smoke_tests.py` runs all 6 scenarios **5× in a row** — must pass clean; HITL approval gate proven for every write; cut `release/alpha-1-freeze` branch from this commit
- GUI: record demo video b-roll (10–15 min) of the agent doing each scenario through the GUI — saves Day 10 video time
- **Tag:** `v0.4.0-alpha.1` + cut `release/alpha-1-freeze` + GitHub Release
- 🎯 At this point the project passes the grading floor.

**Day 10 — Tech report + final polish + submission**
- No new features. Only P0/P1 fixes
- Finalize `docs/technical_report.md` → PDF (use `docx`/`pdf` skill or `pandoc`); polish README with install + supported commands + example I/O
- Edit demo video from Day 9 b-roll (10–15 min final cut) or rehearse for live presentation
- Final GUI polish based on the video review
- Build submission ZIP via `scripts/create_release_bundle.py` (excludes `.git`, `.venv`, `node_modules`, `vectorstore/`, `screenshots/`, `backups/`, `.env`)
- **Tag:** `v1.0.0-demo` — final submission

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
| WebUI prerequisites missing → only Dashboard visible | High | Critical | **Day 1 §3 pre-flight checklist; do not start Day 4 until verified** |
| WebUI selectors break across IOS XE versions | Medium | High | Multiple selector strategies per element; `selector-map.md` doc; codegen rerun if upgraded; **selector discovery moved to Day 4 (was Day 7) — breakage surfaces 3 days earlier with Day 9 as dedicated buffer** |
| Self-signed cert blocks Playwright | High | Medium | `ignore_https_errors=True` from Day 1; Chromium flag fallback; **cert handling validated on Day 3 probe** |
| Cisco WebUI session timeout (5 min idle) | High | Medium | Relogin detection; session keepalive helper; **timeout patterns surface on Day 5 hostname flow (was Day 8) — relogin handler has 3 extra days to bake before alpha freeze** |
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
