# Next session kickoff — 2026-05-24+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. You are operating as the **Orchestrator / Head Architect** of an Engineering & Networking Team reporting to the Director (Filip).

**FIRST**: check your skill list for **THREE** project-relevant skills:
- `director-blueprint` (operating model — role split, communication, per-chunk workflow, Sonnet briefing template, audit templates by tier, tag discipline, bug-fix loop, anti-patterns).
- `external-review-triage` (workflow for a multi-row code-review summary table — verify each via Haiku, bundle by severity, ship sequentially with deep audit per chunk).
- `live-smoke-iteration` (NEW 2026-05-23) — ship→smoke→triage workflow when iterating against a live system. Encodes today's vision-stack saga lessons: visibility-first, evidence-per-smoke, wiring-trap prevention, backup-tag discipline. Triggers on "live smoke", "live router", repeated smoke failures, or terminal-log paste-bombs.

Invoke `director-blueprint` via the Skill tool BEFORE drafting your response. Also invoke `live-smoke-iteration` if the first user message involves a live smoke result or paste-bombed router logs. If a review summary table appears in conversation, invoke `external-review-triage`. Skills supersede inline memory references where they overlap.

Then read the project-specific references:

1. [CLAUDE.md](CLAUDE.md) — project tone, branch rules, commits, **Communication style** section (team voice, tradeoffs first, no fluff).
2. [~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md](~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md) — Director Blueprint memory (skip if skill loaded).
3. [docs/roadmap-2026-05-19-bug-list.md](docs/roadmap-2026-05-19-bug-list.md) — master 18-chunk roadmap.
4. The "What landed 2026-05-19/20/21/22/**23**" sections in **this** kickoff doc — five days of recap. The 2026-05-23 section is the longest by far — the entire day was the vision-stack saga.

After reading, summarise back in 10-12 sentences:

1. `feature/bootstrap` at HEAD **`79dc895`** (14h-F hybrid revert), **690 tests passing** (+57 over the 633 baseline of 2026-05-22 evening). Last tags: `v0.5.8-session-window-fix` at `9b6d8ec` (2026-05-22) + backup tags `backup-20260523-1259` at `aff5f53` (mid-day) and `backup-20260523-1842` at `79dc895` (end of 2026-05-23).
2. The Director Blueprint operating directive — team voice, tradeoff tables BEFORE architectural decisions, no corporate fluff. **Audit tier rule (locked):** Haiku 4.5 light / Opus 4.7 deep. When in doubt → deep. **2026-05-23 lesson:** 14k shipped dead code because audit was skipped on "small surface" — DO NOT skip deep audit on any smoke-touching chunk.
3. Phases A, B, C, D, E, G + chunks 1/1.5/9/10/14/16 are LANDED from earlier days. The 2026-05-23 vision stack adds: 14b (vision_fallback module), 14f-adaptive (plan vision pre-check), 14g (vision-first inversion — OBSOLETED by 14h-F), 14h-C/D/E/F (visibility + uniqueness + eviction + hybrid revert). NONE of these are tagged as a release yet — all under the `backup-20260523-1842` end-of-day tag.
4. **The vision stack is architecturally complete but DHCP smoke is still RED.** Last live evidence (`act_20260523_589a83`): vision resolved selector `button:has-text('Add')` which doesn't match Cisco's nested-icon buttons; 14h-F restores eid-first lookup which SHOULD skip vision entirely for Add (e_020 is in describe view). **First action this session: re-smoke DHCP at 79dc895.** If green, tag `v0.5.9-vision-hybrid`. If red, the click is failing on the REAL eid locator — pure Playwright/page-state issue, not vision-related.
5. **14h-C subprocess log forwarding is the most-important shipped artifact of the day.** Without it the whole vision stack was invisible on live router. Going forward EVERY live-smoke iteration has `vision_fallback_*`, `selector_cache_evicted`, `plan_vision_check_*` events visible in parent uvicorn log. Use them.
6. The Opus → Sonnet → tiered-audit flow is standard. **2026-05-23 added discipline:** for every new function/contract, grep for runtime callers BEFORE commit. The 14k wiring trap (role_text strategy handler + dhcp_form YAML — both dead code in the runtime path) caught nothing in tests but burned a smoke iteration. The `live-smoke-iteration` skill encodes this.
7. **Vision-from-screenshot fundamentally can't see HTML attributes.** When vision is asked for selectors that require DOM knowledge (e.g. `input[name='networkIp']`), it falls back to text-based selectors (`button:has-text('Add')`). On Cisco's hostile-DOM (icon-only buttons, no proper labels) those don't match. Hybrid architecture (eid-first → vision-fallback) is the right shape: eid lookup uses the describe view which DOES have HTML attribute knowledge.
8. **Two LOW audit follow-ups** inherited from 14g + 14h-F (vision-path `get_attribute` fail-open bypasses deny-list; `_eid_for_intent` tie-break doesn't filter deny-listed candidates). Neither blocks; track for 14h-G cleanup chunk after smoke green.
9. **The architectural exhaustion list** — what's been tried, what works, what doesn't (read full 2026-05-23 recap):
   - 14b reactive vision_fallback (only fires on unknown_eid) — RIGHT shape
   - 14g vision-first inversion — WRONG (skips correct eid path)
   - 14k YAML + role_text — DEAD CODE in runtime
   - 14h-F hybrid (eid-first → vision-fallback) — RIGHT, awaiting smoke validation
10. **Next candidates after smoke validates 14h-F:**
    - `v0.5.9-vision-hybrid` tag (after green smoke)
    - 14g/14h-F LOW audit follow-ups (1h cleanup chunk)
    - 14h-A vision-grounded configure_planner (give planner the screenshot + element list + DOM — the long-term fix for "planner emits broken plans" that has been the root cause of multiple failure classes)
    - Chunk 15 (hardware retests for OSPF, ISIS — was blocked on DHCP working)
    - #8 SecretStr migration (deferred from 2026-05-21 review pass)
    - Pre-demo hardening MED/LOW batches before `v0.4.0-alpha.1`

Then wait for "go". **Do not propose re-planning chunk order** — locked unless Filip asks.

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

## What landed 2026-05-21

10 commits, 2 milestone tags, +14 tests (588 → 602). Phase G chunk 12 shipped + chunk 16 (README + screenshots) + the `director-blueprint` skill installed globally.

### Phase G chunk 12 — auto-debug on write failure (`v0.5.6-phase-g-autodebug` at `d62963b`)

**Initial implementation (`5975b33`)** — NEW `backend/orchestration/debug_planner.py` with three modes (focused via `draft_debug_plan`, broad via `draft_debug_sweep`, digest via `draft_debug_summary`). NEW `_propose_debug_sweep` + `_debug_sweep` in tool_registry; `debug_sweep` added to `WRITE_TOOLS` for the two-click contract. `mark_failed(action_id, result=None)` extended in confirmations.py to persist the result dict on the action for later retrieval. `routes_approvals` execute path passes the structured error result. Planner system prompt amended (rule 8) to instruct the LLM to call `propose_debug_sweep` on `verify_failed`/`tool_failed`. Frontend `onExecute` catch auto-sends `"Please diagnose action_id=X..."` when err contains `verify_failed`/`tool_failed`. `verify_output_preview` cap bumped 1000 → 3000 chars and inline snippet 200 → 400. "Diagnose router state" added to suggestion seed. 11 new unit tests. Haiku audit: PASS.

**Live smoke surfaced 4 cascading bugs over 4 follow-up commits** — read these in order if you're touching the auto-debug code:

- `3a682cf` — frontend wasn't surfacing the `_debug_sweep` digest. `onExecute` was throwing away the execute response; result message only rendered `pending.summary` (propose intent) not `resp.result.summary` (Haiku digest). Fix: capture response, extract `result.summary`, add `digest` field to result message, render in new amber DIAGNOSIS block (`.result-digest` CSS). Plus strengthened the `propose_debug_sweep` tool description to mandate `failure_action_id` extraction from auto-debug user messages.
- `14c1ce7` — even with the stronger tool description, Haiku didn't reliably pass `failure_action_id` from the user message. Server-side fallback added: NEW `find_most_recent_failure()` in confirmations.py scans `_actions` for the most-recent FAILED action with a stored result; `_propose_debug_sweep` calls this when the kwarg is omitted. +2 tests lock the fallback.
- `d62963b` — the **load-bearing** fix. `find_most_recent_failure()` was still returning None in live smoke. Root cause: `write_tools.cli_configure` calls `mark_failed(action_id)` (no result) BEFORE returning the verify_failed dict, transitioning state to FAILED. Then `routes_approvals.py:213` checks `get_state == "EXECUTING"` and SKIPS its own `mark_failed(action_id, result)` because state already changed. Action ends up FAILED with `result=None`; fallback filters it out; broad sweep wins. Fix: restructure `write_tools.cli_configure` to build the result dict FIRST, then call `mark_failed(action_id, result)` — so the result is stored from the source. Other write tools' `mark_failed(action_id)` exception-handler calls stay as-is (different code path; those raise rather than return structured dicts).

After all four fixes, live smoke on the static-route failure (`ip route 5.5.5.5 255.255.255.0 6.6.6.6` → `%Inconsistent address and mask`) produces a focused diagnostic plan (`show ip route static | include 5.5.5` + `show running-config | include ip route`) and a Haiku digest that explicitly explains the host-bits-set error and recommends `5.5.5.0/24` as the fix. End-to-end in ~5 seconds.

### Chunk 16 — README refresh + 5 screenshots (Filip-driven)

Filip updated `README.md` himself with a refreshed top description, a 5-screenshot walkthrough (Dashboard, AI Configuration, Config Preview, Devices, Settings), and a polished Install/Configure/Run/Troubleshooting block. Screenshots saved to `docs/screenshots/`. Both still uncommitted in the working tree — next session should commit them as opening housekeeping along with the kickoff doc update.

### NEW skill: `director-blueprint`

Installed at `~/.claude/skills/director-blueprint/SKILL.md`. Captures the whole 3-day operating model: role split (Director / Opus / Sonnet / Haiku), communication style (team voice, tradeoffs first, no fluff), per-chunk workflow (Plan → Sonnet briefing → Haiku audit → commit → smoke → tag), Sonnet briefing template, Haiku audit template, tag discipline (hands-off + backup pair), bug-fix loop, anti-patterns. Auto-triggers in any project that has a chunked roadmap. Future Opus instances should invoke it via the Skill tool on message #1 — supersedes re-reading the memory file.

### Audit model: three iterations on 2026-05-21, settled on tiered split

The audit role moved through three rules on the same day:

1. **Morning (start of day):** Haiku 4.5 always (legacy from 2026-05-19 Director Blueprint).
2. **Mid-day:** Opus 4.7 always. Trigger: chunk 12 needed 4 follow-up fixes despite a Haiku PASS on the initial commit — Haiku's surface check missed the `mark_failed(action_id)` vs `mark_failed(action_id, result)` call-order contract violation that produced clean unit tests but live-smoke failures.
3. **Evening (current rule):** **Tiered split** per Director directive — Haiku 4.5 for light audits, Opus 4.7 for deep audits. Reason: Opus on every typo-fix audit is ~50× overkill.

**Audit tier rule:**

| Tier | When to use | Auditor model | Cost | Latency |
|---|---|---|---|---|
| **Light** | 1–3 files, pure cleanup/docs/cosmetic/typo/rename, no new contracts, no new tool wiring | Haiku 4.5 | ~$0.01 | ~30s |
| **Deep** | 4+ files OR new contracts OR new tool wiring OR security-touching OR error paths OR live-smoke-gated | Opus 4.7 | ~$0.40–0.60 | ~60–90s |

When in doubt → deep. Production backend LLM still stays Haiku 4.5 — model-role-split is dev-time only.

**Files updated (docs commit):**
- [CLAUDE.md:27](CLAUDE.md:27) — role split line.
- `~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md` — 7 sections (frontmatter, Opus role bullet, Haiku role bullet, audit step, audit-report step, edge cases, Why-this-split paragraph).
- `~/.claude/skills/director-blueprint/SKILL.md` — 7 sections (frontmatter description, role-split table, role-split rationale, workflow diagram, briefing-template rationale, audit section + new "Why Opus audits" rationale paragraph, reference paragraph).
- This file (kickoff doc) — summarise-back checklist line + this recap entry.

### External review pass (PM) — 4 chunks shipped, 9 findings fixed

A second external code review surfaced a 15-item summary table after `15de1dd`. Verified each finding via Haiku Explore agent: **11 real, 4 misread or already-fixed**. Bundled into 4 chunks by severity.

| Chunk | Commit | Findings | Tier | Tests |
|---|---|---|---|---|
| **A** | [`f8fe1d5`](https://github.com/) | #1 Chromium sessions never closed (CRITICAL) | Deep | +2 (604) |
| **B** | [`a60592e`](https://github.com/) | #3 Empty cred defaults + #2 Action-store TTL (HIGH×2) | Deep | +8 (612) |
| **C** | [`f51e6d9`](https://github.com/) | #5 TOCTOU race + #7 Param signature guard + #6 mypy cleanup (MEDIUM×3) | Deep | +6 (618) |
| **D** | [`9012b6e`](https://github.com/) | #9 WebUI goto timeout + #11 Eventbus log throttle + #14 WS strict-origin toggle (LOW×3) | Deep (escalated, #14 security-touching) | +5 (623) |

**Total: 4 commits, +21 tests (602 → 623), all 4 Opus 4.7 deep audits returned PASS.**

**Key fixes:**
- **A:** `try/except/finally` wrap in `routes_chat.chat()` so `close_all_sessions()` runs after every turn. Atexit hook becomes backup, not primary.
- **B:** `Settings.validate_required_credentials()` method called from main.py lifespan — fails at boot with all 7 missing creds listed (not a Pydantic `model_validator` — would burden every test). Lazy 24h TTL purge in `propose_action` removes terminal actions older than cutoff.
- **C:** New atomic primitive `try_mark_failed_if_executing(action_id, result)` replaces the two-`get_state` TOCTOU. `execute_tool` adds `inspect.signature` guard that returns `bad_parameters` error on extra splat keys. Two mypy `ignore_errors` overrides cleared (`backend.core.logging`, `backend.webui_agent.selectors`); 5 remain.
- **D:** `webui_goto_timeout_ms` + `ws_strict_origin` settings added. Eventbus throttles backpressure warnings to one per 5s with aggregated `drops_since_last_log` count. WS strict-origin opt-in defaults to False (dev workflow unchanged); flip to True for non-localhost deployment.

**Deferred from this pass:**
- **#8 — Name-based redaction misses renamed secret fields.** Fix is `pydantic.SecretStr` migration for `anthropic_api_key`, `router_ssh_password`, `router_webui_password` + ~15 call-site updates to `.get_secret_value()`. Scope too large for the MEDIUM batch; tracked as a follow-up chunk before `v0.4.0-alpha.1`.

**Misread / already-fixed (no work):**
- **#4** — validators are centralized in `write_tools.py` and imported into `tool_registry.py`; not duplicated.
- **#10** — PyTorch CPU install IS documented in a comment block in `requirements.txt` (just not in a standalone doc).
- **#12** — `WriteRejectedError` is actively used in 3 handlers, not dead.
- **#13** — Static mount is already last with explicit "keep this LAST" comment.
- **#15** — `test_close_all_sessions_closes_every_cached_session` already exists in `tests/unit/test_generic_driver.py`.

**Audit rule worked end-to-end.** Tiered rule (Haiku light / Opus 4.7 deep, set earlier today) was applied: A/B/C/D all routed to Opus 4.7 deep because of security-touching surfaces (routes, settings boot guard, WS origin). One audit (Chunk D) was tier-escalated mid-flight when the WS strict-origin scope clarified — orchestrator's call, per the rule.

### Evening fixes — describe race + session lifecycle (tag `v0.5.8-session-window-fix`)

Two live-smoke regressions surfaced after the v0.5.7-review-hardening tag. Both fixed and tagged the same evening.

#### Path B — describe settle+retry + message propagation (`3b2cc9c`)

**Symptom:** DHCP intent `act_20260521_921e52` failed with chat showing `describe_failed: no message`. Two bugs in one error:

1. **UX bug:** `tool_registry.py:1414` wrapped `webui_describe_page`'s error result but DROPPED the inner `message` field — only kept `describe_error` (structured). The route-layer fallback `{message or 'no message'}` then surfaced "no message" to chat.
2. **Underlying race:** `_run_session_loop` `op == "open"` went `page.goto(wait_until="domcontentloaded")` → `describe_page` with NO settle. The DHCP form is AngularJS — `domcontentloaded` fires before controllers mount, so describe returns an empty view, which the wrap catches as describe_failed.

**Fix:**
- Propagate inner `message` in the wrap with fallback string `"describe_page returned no usable view"`.
- Insert `_settle_page(page)` between goto and ev.step in `op == "open"`.
- New `_describe_with_retry(page, max_attempts=2)` helper: settles + re-describes if first view is empty (no elements AND no modals). Used in BOTH `op == "open"` and `op == "describe"`.
- +4 tests (627 → 631). Opus 4.7 deep audit PASS.

#### Chunk A2 — session lifecycle for propose→execute multi-turn (`9b6d8ec`)

**Symptom:** After Path B, DHCP retest `act_20260521_5ccca4` failed with `describe_failed: no live session for session_id='sess_X'`. Path B's inner-message propagation worked correctly — the real error became visible.

**Root cause:** Chunk A (`f8fe1d5`) added unconditional `close_all_sessions()` in `routes_chat.chat()` finally. That broke the propose→approve→execute multi-turn flow:

1. Chat 1: `propose_webui_configure` opens session `sess_X`, stores `session_id` in action's params, returns proposal.
2. Chat finally → `close_all_sessions()` → `sess_X` dies.
3. User approves + executes (separate HTTP call, no chat turn).
4. `_webui_configure` reads `session_id=sess_X` from params → session not in `_sessions` → `_session_not_found` → describe_failed.

**Fix:**
- Chat finally close is now CONDITIONAL on `_pending_approval(result.events) is None`. If approval is pending, leave the session alive.
- `routes_approvals.execute` and `routes_approvals.reject` BOTH wrap their function bodies in `try: ... finally: close_all_sessions()` — sessions die when the action enters a terminal state.
- Double-try structure in chat: outer try/finally for cleanup; inner try/except for the existing six exception handlers. Flag `keep_sessions_for_approval` set on success path inside the inner try, read in the outer finally.
- +6 tests (631 → 633). Opus 4.7 deep audit PASS.

**Critical attribute bug avoided:** Sonnet correctly used `_pending_approval(result.events) is not None`, NOT `result.awaiting_approval` — `PlannerResult` (planner.py:316-320) has no `awaiting_approval` field; it's only an event `kind`. Spec's CRITICAL CHECK called this out and Sonnet got it right.

#### Live-smoke status after v0.5.8

DHCP form `act_20260521_5ccca4` retry:
- ✅ Session survives propose→execute (no session_not_found).
- ✅ Describe-retry kicks in on slow DHCP page (no describe_failed).
- ⚠️ Inner WebUI planner mis-fills fields: Network empty, Starting IP shows the subnet mask `255.255.255.0` instead of an IPv4 address. **Same class as OSPF screen-routing bug — chunk 14b territory.**

### Chunk 14g — Vision-first selector resolution (LANDED, awaiting live smoke)

Shipped 2026-05-23 after the live smoke `act_20260523_f8cd97` proved chunk 14k (commit `25f9e50`, YAML + role_text + spatial-label) was dead code in the runtime path. Both new additions in 14k bypassed the `_do_act_by_intent` flow entirely — the YAML is only consulted by named-flow POM modules, and the `role_text` strategy was added as a handler in `login._build` but never wired into the strategies list at `_playwright_subprocess.py:753`. Tactical patches couldn't fix the underlying architectural fragility of heuristic-first selector resolution on hostile AngularJS forms.

14g inverts the architecture. Vision becomes the PRIMARY selector resolution mechanism; heuristics (`eid lookup → first_match strategies`) become vestigial fallback. The existing `selector_cache.json` from 14b makes repeat calls free. Eviction-and-retry handles stale cached selectors when forms re-render.

**Flow change:**
- BEFORE: describe_page eid → first_match → vision_fallback (only on None) → unknown_eid
- AFTER: `resolve_via_vision` (cache-aware, internal) → fresh_map eid → first_match → unknown_eid

**Eviction loop:** if a vision-resolved selector's fill returns `element_hidden | element_disabled | element_intercepted`, evict the cache entry, re-call `resolve_via_vision` (cache miss → Anthropic), retry `_do_act` once. Self-healing on form re-render or version drift.

| File | State | Size |
|---|---|---|
| `backend/webui_agent/_playwright_subprocess.py` | Modified | +135/-47 lines (refactored `_do_act_by_intent`) |
| `backend/webui_agent/vision_fallback.py` | Modified | +24 lines: new `evict_from_selector_cache` helper, `_MAX_VISION_CALLS_PER_SESSION` bumped 5→15 |
| `tests/unit/test_vision_fallback.py` | Modified | +2 tests (evict helper) |
| `tests/unit/test_playwright_subprocess.py` | Modified | +6 tests (vision-first path, fallthrough, evict+retry, no-evict-on-missing, no-infinite-loop, security regression for deny-list) |

**Tests:** 673/673 unit suite green. Ruff + mypy clean.

**Opus 4.7 deep audit:** CONDITIONAL PASS → 2 HIGH findings fixed before commit:
- **Security: deny-list bypass.** Vision-resolved locators skipped the `_SENSITIVE_DENY_LIST` check (Reboot / Factory Reset / etc.). Mirrored the heuristic-path probe inside `_try_act_with_vision` before calling `_do_act`. Added regression test `test_act_by_intent_vision_path_enforces_sensitive_deny_list`.
- **Ops: per-session cap too tight.** Bumped `_MAX_VISION_CALLS_PER_SESSION` 5→15. At cap=5 the first-ever DHCP run would burn budget on fields 1-5, field 6 falls through to heuristics → unknown_eid → same failure 14k hit. Worst-case spend rises from $0.075 to $0.225/session.

5 MEDIUM/LOW findings tracked as 14h follow-ups (retry observability gap, eviction race, log volume — all non-blocking).

**Live-smoke target:** DHCP intent on C1111-4P. Expected:
- First fill: vision resolves each field, caches. Slow (~30s for 5-6 fields × 2s vision).
- Form fills correctly. Apply lands clean. `verify_present: true`.
- Second run: cache hits, fast (~5s).

If green: propose `v0.5.9-vision-first` tag covering 14b + 14f-adaptive + 14g (and obsoleting 14k's dead-code attempt).

### Chunk 14f-adaptive — Vision pre-check on planner output (LANDED, awaiting live smoke)

Shipped 2026-05-23 after triage of `act_20260523_484286` (DHCP smoke failure). 14b never fired in that smoke because `first_match` returned wrong-but-non-None EIDs — vision_fallback only catches `unknown_eid`. The real bug was upstream: inner Haiku `configure_planner` produced a wrong plan (skipped Network field; iter 3 re-draft put subnet mask value `255.255.255.0` into Starting IP).

14f-adaptive inserts a vision pre-check on the drafted plan BEFORE any step dispatches, at TWO sites:
- **Proposal-time** in `_propose_webui_configure` — operator sees REJECT in chat BEFORE approving.
- **Per-iter** in `_webui_configure` while-loop — catches re-drafts (the actual DHCP failure mode).

**Adaptive intensity** scales by historical familiarity for the (page, intent) pair:
- Tier 0 (familiarity ≥0.85): skip vision entirely.
- Tier 1 (0.55–0.85): plan-level PROCEED/REJECT.
- Tier 2 (0.25–0.55): step-by-step validation.
- Tier 3 (<0.25): adversarial "find what could go wrong" with RAG + running-config context.

Familiarity formula: `0.40·cache_hit + 0.25·success_ratio + 0.20·snapshot_count + 0.15·plan_validation`. Per-action budget cap 5 successful API returns (separate from 14b's 5/session). Default-PROCEED on all failure paths (API error, malformed JSON, low-confidence REJECT, session cap, kill switch).

| File | State | Size |
|---|---|---|
| `backend/orchestration/plan_vision_check.py` | NEW | 675 lines |
| `tests/unit/test_plan_vision_check.py` | NEW | 19 tests (18 spec + 1 model-pin guard) |
| `backend/orchestration/tool_registry.py` | Modified | +210 lines (3 sites: proposal, per-iter, success-cache hook) |
| `backend/core/settings.py` | Modified | +5 lines (`plan_validation_cache_path` + `plan_vision_enabled` kill switch) |
| `backend/webui_agent/evidence.py` | Modified | +1 line (`plan_vision_count`) |

**Tests:** 19/19 on `test_plan_vision_check.py`, 655/655 full unit suite. Ruff clean.

**Opus 4.7 deep audit:** CONDITIONAL PASS → 2 fixes shipped before commit:
- **#1 CRITICAL fixed**: proposal-time REJECT path now mirrors per-iter (logs, dumps rejection, closes sessions, returns `plan_rejected_by_vision` error).
- **#4 HIGH fixed**: screenshot search scoped to current `session_id` subdir (was rglob across all of `artifacts/screenshots/`, could grab cross-session PNGs).

**Live-smoke targets:** Re-run DHCP intent (`act_20260523_484286` shape) + OSPF intent on C1111-4P. Expected: Tier 3 fires (zero familiarity), vision REJECTs the no-Network plan OR REVISEs with correct plan. After both succeed, `artifacts/plan_validation_cache.json` populated with `succeed_count: 1` entries. Third successful run promotes to Tier 0 (free).

If green → propose `v0.5.9-plan-vision-pre-check` tag (Filip's call).

### Chunk 14g — vision-check polish (6 audit follow-up items)

Tracked from the 14f-adaptive deep audit. None blocks live smoke; bundle when convenient.

1. **URL fragment dropped by `_hash_page_url`** (`vision_fallback.py:54-61`). Every AngularJS SPA route (`#/dhcp`, `#/ospf`) collapses to the same `page_k`. Both 14b's selector_cache AND 14f's plan_validation_cache have dead `page` dimension. Fix: include `parsed.fragment` in the normalized form. Add unit test asserting `_hash_page_url('/webui/#/dhcp') != _hash_page_url('/webui/#/ospf')`.
2. **snapshot_signal gaming hole** (`plan_vision_check.py:193-210` + `write_tools.py:408, 497, 586, 676`). `_snapshot_signal` counts ALL `device-snapshots/<id>/post/` dirs, but `take_snapshot(action_id, "post")` also fires on `WriteRejectedError`. 5 failed actions → +0.20 familiarity bump. Restrict to EXECUTED-only OR write a success-only sentinel file.
3. **`_plan_vision_counters` memory leak** (`tool_registry.py:69, 1418`). Module-level dict grows unboundedly with action_ids. Add `_plan_vision_counters.pop(action_id, None)` in a `try/finally` wrapping the while-True loop.
4. **No per-window cap at proposal time** (`tool_registry.py:1210-1220`). Re-propose loop could burn Anthropic budget unmetered. Add `collections.deque[float]` of timestamps with N-per-5-min cap.
5. **`_extract_first_json_object_local` is verbatim copy** (`plan_vision_check.py:390-416` vs `configure_planner.py:201-227`). Promote to shared `backend/orchestration/_json_extract.py`.
6. **Atomic cache cross-session race** — carried over from 14b (already tracked). Same `.tmp+replace` pattern; consolidate fix across 14b's `selector_cache` and 14f's `plan_validation_cache`.

### Chunk 14b — Vision fallback (LANDED, awaiting live smoke)

Triaged 2026-05-22 morning. Decision: review-and-commit (architecture sound, anti-pattern checklist clean, deviations from 2026-05-19 sketch are scope reductions not mistakes). Cleanup pass added the per-session cost cap, ruff/import fixes, and the integration-site `log.warning`. Opus 4.7 deep audit returned **PASS** — all findings MEDIUM/LOW, none gate live smoke.

| File | State | Size |
|---|---|---|
| `backend/webui_agent/vision_fallback.py` | NEW | 323 lines |
| `tests/unit/test_vision_fallback.py` | NEW | 17 tests (15 base + 2 cap) |
| `backend/core/settings.py` | Modified | +4 lines (`selector_cache_path` field) |
| `backend/webui_agent/_playwright_subprocess.py` | Modified | +34 lines (vision branch in `_do_act_by_intent` + log.warning) |
| `backend/webui_agent/evidence.py` | Modified | +14 lines (`vision_screenshot` + `vision_call_count`) |
| `tests/unit/test_playwright_subprocess.py` | Modified | +2 lines (patch `resolve_via_vision` in existing unknown_eid test) |

**Tests:** 17/17 on `test_vision_fallback.py`, 636/636 full unit suite. Ruff clean.

**Architecture (locked):**
- Reactive — invoked only by `_do_act_by_intent` on `unknown_eid` (after semantic-DOM + `first_match` both return None).
- Cache hit short-circuits Anthropic. Cache key `role|name|sha1(scheme+host+path)[:12]` — stable across query strings.
- Confidence threshold 0.7. Self-rated by Haiku, not calibrated.
- Per-session cost cap: 5 successful API returns per `EvidenceCollector` instance. Increment fires inside the try-block after `_call_haiku_vision` returns successfully — exceptions don't increment.
- Atomic cache writes via `.tmp` sibling + rename. Single-session-safe; cross-session race is a known follow-up (see 14c below).
- Grounding context on every cache-miss call: current screenshot + up to 2 prior screenshots of the same page (path-tail match in `artifacts/screenshots/`) + freshest `post/show_running-config.txt` (8 KB cap).

**Known live-smoke targets:**
- DHCP form `act_20260521_5ccca4` — Network field empty, Starting IP filled with subnet mask.
- OSPF intent — same field mis-fill class.

If smoke green: propose `v0.5.9-webui-vision-fallback` tag (Filip's call to create it). If red: capture cache state + screenshots, do not auto-retry.

### Chunk 14c — follow-up items from the 14b deep audit (PASS verdict, ship list)

Tracked here so they don't get lost. None blocks 14b; bundle when convenient.

1. **Cross-session cache race comment** (`vision_fallback.py:75-82`). Two parallel sessions both `load_selector_cache → mutate → save` → second rename clobbers first key. Demo-lab is single-session so it can't fire today. Add a comment noting the assumption; file-lock if multi-tenant ever lands.
2. **`error_type` in vision-exception log** (`_playwright_subprocess.py:783-785`). Current warning logs `error=str(exc)`. Match the existing pattern at `_playwright_subprocess.py:397` which logs `exc_type=exc.exc_type`. One-line fix.
3. **Integration test for vision-success path.** Today's `test_act_by_intent_returns_unknown_eid_when_first_match_returns_none` only exercises the `None → unknown_eid` fallthrough. Add a counterpart: patch `resolve_via_vision` to return a selector, mock `page.locator` + `_do_act`, assert `reply["resolved_via"] == "vision"` and `reply["chosen_eid"]` starts with `"vision_"`.
4. **Secret-page deny-list.** Vision_fallback docstring (lines 17-19) acknowledges screenshots may contain raw secrets on pages like AAA/RADIUS/IPsec PSK. Add a URL-path deny-list (e.g. refuse vision on `*/aaa/*`, `*/ipsec/*`, `*/radius/*`).
5. **Cap-counts-success-not-billing nuance.** Current cap measures successful API returns. If Haiku returns malformed JSON 5×, the JSON parse failure raises BEFORE the increment, so 5 Anthropic calls were billed but `vision_call_count` reads 0 → the cap doesn't bound true Anthropic spend in this degenerate case. For demo lab, the gap is ≤5 wasted calls. Document the trade-off in a code comment OR move the increment inside `_call_haiku_vision` right after `response = client.messages.create(...)` so it fires before JSON parse.
6. **Offline corpus bootstrap** (the original 14c content per the previous roadmap entry). Walk past `artifacts/screenshots/` + running-configs, pre-populate selector_cache from accumulated data so day-one runs hit cache. Defer until live smoke gives us a real cache-hit rate from reactive learning alone.

### NEW skill installed 2026-05-21 PM: `external-review-triage`

Distilled from today's 15-item review pass. Captures: verify-each-finding-via-Haiku → bundle-by-severity → ship-with-deep-audit-per-chunk → update kickoff doc → propose tag. Plus 8 anti-patterns observed (blanket-close-on-finally, two-step state check, params splat, name-based redaction, empty credential defaults, static mount shadowing, WS missing-origin bypass, eventbus log flood). At `~/.claude/skills/external-review-triage/SKILL.md`. Auto-triggers when a review summary table appears in the conversation.

### Architectural lessons captured (2026-05-21 evening additions)

- **Blanket-close on chat-finally is wrong for resources that span chat turns.** Conditional cleanup based on whether the work is FINISHED, not whether the current request is exiting. Chunk A → A2 lesson.
- **Inner error result `message` fields must be propagated by every wrapping layer.** A 5-line `tool_registry.py:1414` wrap dropped the field; chat displayed "no message"; Filip's diagnosis was correct but blind. Always propagate `message` (with fallback) in result-dict wraps.
- **`page.goto(wait_until="domcontentloaded")` is NOT enough for AngularJS pages.** Always settle (networkidle + fallback sleep) before the first describe. The DHCP race lived since day one but only surfaced when Filip tried DHCP — VLAN and hostname pages happened to mount fast enough.
- **`PlannerResult` field shape vs event shape.** Code that reads `result.awaiting_approval` will AttributeError; the data lives in `result.events` as an event with `kind="awaiting_approval"`. Always read via the `_pending_approval(events)` helper, never directly.

### Pre-demo hardening punch list (verified)

Two slash-command reviews (`/review` + `/security-review`) ran against `v0.5.5` on 2026-05-21. Both concluded "ship as-is for alpha-1 demo." Verified findings + corrections added to the "Pre-demo hardening" section below — pick up before cutting `v0.4.0-alpha.1` (chunk 18) or any external sharing.

### Architectural lessons captured (2026-05-21 additions)

- **`mark_failed(action_id)` vs `mark_failed(action_id, result)` — the call ORDER matters.** When write_tools and routes_approvals both call `mark_failed`, whichever fires FIRST transitions the state. The second caller's structured result gets dropped because the state check guards against duplicate transitions. Pass the result from the source.
- **Server-side fallbacks beat fighting the LLM.** Haiku ignored the strong tool description mandating `failure_action_id` extraction. Adding a 5-line server-side scan of FAILED actions in confirmations was more reliable than 5 iterations of prompt-tuning.
- **Live smoke surfaces architectural defects unit tests miss.** Chunk 12 needed 4 follow-up commits despite all unit tests passing. Each fix improved a real defect, not just a symptom. Plan for this rhythm on UX-heavy chunks.

## What landed 2026-05-23

**Theme of the day: the vision-stack saga.** 9 commits between two backup tags (`backup-20260523-1259` mid-day at `aff5f53`, `backup-20260523-1842` end-of-day at `79dc895`). Started with 14b partial in worktree, ended with a hybrid architecture pending live-smoke validation. Tests 633 → 690 (+57). **DHCP smoke still RED at session end.** The day's hard-won lessons captured in NEW skill `~/.claude/skills/live-smoke-iteration/SKILL.md` — invoke it from message #1 of any session that involves smoke iteration.

### The full commit chain

| Commit | Chunk | What | Outcome |
|---|---|---|---|
| `b8ef295` | 14b cleanup | vision_fallback module (reactive on unknown_eid) + per-session cap + atomic cache writes | Tests green, never smoke-validated (vision didn't fire because subprocess logs were silent) |
| `298681e` | 14f-adaptive | plan vision pre-check with 4-tier familiarity scoring | Tier 3 adversarial REJECT verdicts caught real planner bugs; router stayed clean |
| `e81be0a` | 14f auth fix | `api_key=` passed to `Anthropic()` (was relying on broken env-var resolution) | First smoke that actually called Haiku vision |
| `27a0421` | 14f JSON + gaming | Brace-extract JSON from prose; snapshot_signal filters to EXECUTED-only actions | Plan vision works on prose responses; familiarity gaming closed |
| `dfd9bda` | CI mypy | 27 mypy errors fixed (Anthropic SDK TypedDict + union-attr issues) | CI unblocked on Py 3.12 |
| `25f9e50` | 14k (FAILED) | DHCP YAML + `role_text` strategy + spatial-label JS exclusion | **DEAD CODE.** Audit was skipped. Both additions never wired into runtime path. Burned one router smoke. |
| `f84eb00` | 14g | INVERTED selector resolution: vision-FIRST, heuristics as vestigial fallback | Wrong call — skipped correct eid path for describable elements (Add button) |
| `aff5f53` | Option H | When vision REJECTs with suggested_plan, treat as REVISE (use the suggestion) | Vision's suggested plans correctly addressed planner bugs |
| `backup-20260523-1259` | tag | mid-day safety net at aff5f53 | — |
| `5bef78f` | 14h-C | Subprocess stderr → parent log forwarding (daemon thread + NDJSON re-emit) | **BREAKTHROUGH.** First smoke where `vision_fallback_*` events were visible. Burned ~6h before this fix. |
| `7f92118` | audit fix | Kwarg collision when subprocess emits a field named `subprocess` | Opus 4.7 audit caught it pre-push |
| `ac48214` | vision JSON | Same prose-JSON recovery in vision_fallback that 27a0421 added to plan_vision_check | Vision finally returned usable selectors |
| `5b53d90` | 14h-D | Vision prompt rewritten to demand UNIQUE selectors + `_SESSION_OP_TIMEOUT_S` 30→90s | Haiku ignored the uniqueness clause and returned `button:has-text('Add')` anyway |
| `cf7e6a5` | 14h-E | Added `unknown_error` to cache eviction STALENESS set (cache-poisoning fix) | Cache self-heals on next failure; deleted poisoned `selector_cache.json` once |
| `79dc895` | 14h-F | **HYBRID REVERT** — eid-first → vision-fallback → first_match-last (restore 14b shape) | Architecture corrected. Smoke pending at session end. |
| `backup-20260523-1842` | tag | end-of-day at 79dc895 | — |

### What the 4+ DHCP smokes proved

Every smoke today targeted the same DHCP intent. The failure modes evolved as we shipped fixes:

1. **Smoke 1 (pre-14h-C)**: Vision-first fired but silently failed (subprocess stderr discarded) → cascading session_not_found → fall back to heuristics → heuristics picked `e_013 link "Network/Subnet Mask"` (column header) → unknown_error → `iteration_cap_hit`. **Couldn't tell vision was broken.**

2. **Smoke 2 (after 14h-C log forwarding)**: First time we saw `vision_fallback_api_error: "Expecting value: line 1 column 1 (char 0)"` — Haiku returning empty/prose. Caused by `_call_haiku_vision` doing `json.loads(raw_text)` directly. Fix: ac48214.

3. **Smoke 3 (after 14h-D uniqueness prompt + 90s timeout)**: Vision returned `button:has-text('Add')` for the Add button. Click failed with `unknown_error` because Cisco's button has nested `<span><i>...</i>Add</span>` children — `:has-text()` matched zero direct-text elements. Eviction triggered + retry returned SAME selector. `inner_plan_empty`.

4. **Smoke 4 (after 14h-E cache eviction)**: Cache properly evicted (`selector_cache_evicted` event visible — 14h-C working). But vision STILL returned `button:has-text('Add')` because Haiku-from-screenshot fundamentally can't see HTML attributes — it can only describe what's visually rendered.

5. **Smoke pending (at 14h-F)**: Hybrid revert should bypass vision entirely for Add (e_020 is in describe view, eid forward-lookup finds it). Vision only fires for fields like Network that aren't in describe.

### Architectural lessons captured (full 2026-05-23 set)

- **Vision-from-screenshot fundamentally can't see HTML attributes.** When asked for attribute-based selectors (`input[name='X']`), Haiku falls back to text-based selectors (`button:has-text(...)`) because that's what's visible. On Cisco's icon-only buttons those don't match. Vision needs DOM context to produce attribute selectors.

- **Hybrid > pure-vision-first.** 14g's inversion was wrong. The correct order is: eid forward-lookup FIRST (uses the describe view which DOES have HTML attribute knowledge) → vision fallback (for cases where describe drops the element) → first_match heuristics (last resort).

- **Visibility is foundational.** 6+ hours wasted because subprocess `vision_fallback_*` events were silenced by `stderr=DEVNULL`. A ~150-line subprocess-log-forwarding chunk (14h-C) would have saved most of that. New rule: **if two consecutive smokes fail with same generic symptom, STOP architectural changes, ship the visibility fix first.**

- **The wiring trap.** 14k shipped TWO dead-code additions because the audit step was skipped on "small surface, exactly what was recommended". For every new function/contract, GREP for runtime callers before commit. Count call sites.

- **Cache hygiene requires catch-all eviction.** Narrower eviction sets leave poison entries. Include `unknown_error` (the Playwright catch-all) in STALENESS so a bad cached selector self-heals on next failure. Over-evict, don't under-evict.

- **Default-PROCEED on failure paths.** Vision pre-check should never hard-fail on API hiccups (timeout, 529, JSON parse). The action store + operator approval flow are the safety net, not the pre-check.

- **Option H pattern: trust the LLM's suggestion when it provides one.** When vision REJECTs a plan but provides a `suggested_plan`, promote to REVISE and use the suggestion. The LLM saw the form; its suggestion is authoritative.

- **Familiarity-scaled vision intensity must filter to EXECUTED-only signals.** 10 failed retries leaving forensic snapshots should NOT inflate familiarity. snapshot_signal cross-references against `webui_configure_iteration_complete` events with `verify_present=true`.

- **Sonnet auto-commit is a workflow violation.** Briefing explicitly said "don't commit"; Sonnet committed anyway in 14h-C. Net: harmless (audit ran retroactively). Future briefings: emphasize NOT committing, accept rather than revert if it happens with clean code.

### NEW skill installed 2026-05-23 PM: `live-smoke-iteration`

At `~/.claude/skills/live-smoke-iteration/SKILL.md`. Auto-triggers on "live smoke", "live router", repeated smoke failures, terminal-log paste-bombs. Encodes the 5 load-bearing rules (visibility-first, one-smoke-one-evidence, wiring-trap-prevention, backup-tag-discipline, deep-audit-no-skipping) + the vision-stack-specific lessons (vision-from-screenshot constraints, hybrid > pure, self-healing cache, default-PROCEED, familiarity-scaling, Option H). Includes a worked example table of today's 14 chunks. Pairs with `director-blueprint` and `external-review-triage`.

## Remaining chunks (one-line each)

| # | Chunk | Phase | Est | Pri |
|---|---|---|---|---|
| Smoke at 79dc895 | Re-smoke DHCP on C1111-4P with 14h-F hybrid revert | G | ~5 min | **HIGH (first action)** |
| 14h-A (deferred) | Vision-ground configure_planner (screenshot + element list + RAG + DOM → no more broken-plan emission) | G | ~4-6 h | MED (upstream root-cause fix) |
| 14h-G (cleanup) | LOW audit follow-ups: vision-path deny-list fail-open + `_eid_for_intent` tie-break filter | G | ~1 h | LOW |
| 14c | Vision-fallback polish (URL fragment fix, integration test, secret-page deny-list, offline corpus bootstrap) | G | ~2-3 h | MED |
| 14g audit follow-ups | Pre-check polish (URL fragment, snapshot gaming AFTER 14h-E, counter leak, proposal-cap, shared json-extract, atomic cache) | G | ~2 h | MED |
| 15 | Hardware retests — OSPF + ISIS WebUI on live router (unblocked once DHCP green) | F | ~30 min | MED |
| 17 | Cosmetic prototype-label sweep | F | ~10 min | LOW |
| 18 | Cut clean `v0.4.0-alpha.1` consolidation tag | F | ~15 min | — |
| — | #8 SecretStr migration (deferred from 2026-05-21 review pass) | — | ~1 h | MED |
| — | Pre-demo hardening (remaining items in MED + LOW batches) | mixed | ~1 h | mixed |

## Notes / housekeeping

- **First thing in the new session: triage the partial chunk 14b in the worktree.** Run `git status --short` + `git diff HEAD` + `cat backend/webui_agent/vision_fallback.py | head -60`. Verify the integration site in `_playwright_subprocess.py` (look for `resolved_via="vision"` branch). Decide review-and-commit vs discard-and-redo BEFORE touching anything else. See "Chunk 14b PARTIAL" section above for file inventory.
- **`docs/next-session-kickoff.md` is uncommitted** after the 2026-05-21 evening edits adding the v0.5.8 recap and partial-14b state. Commit it as session-start housekeeping after the 14b decision: `git add docs/next-session-kickoff.md && git commit -m "docs: kickoff recap for v0.5.8 + partial 14b worktree state"`.
- `frontend-design-backup/` sweep is its own commit later in the week (after Phase C feels stable).
- `tools/check_vectorstore.py` and `tools/query_rag.py` flagged in 2026-05-19 dead-code audit as worth a follow-up review — not blocking.
- Director Blueprint applies from message #1 of the next chat. **Prefer invoking the `director-blueprint` skill** over re-reading the memory file — same content, kept in sync.
- The plan file `~/.claude/plans/10-commits-4-tags-atomic-widget.md` is outside the repo; safe to delete after the next session starts since the roadmap and per-chunk briefings live in `docs/`. (Same applies to any older plan files in that directory.)

## Post-roadmap polish (after all phases land)

Items deferred until the main roadmap (chunks 14 → 18) is complete. None blocking — pick up only when there's appetite for polish work.

- **Wire `device.id` selection into `fetchSuggestions`** — `frontend/screen-ai.jsx:232-244` currently calls `window.api.fetchSuggestions()` with no argument, so the server defaults to `device_id="router-01"`. Today this is correct (single-device C1111-4P lab; sidebar reads `fetchDevices()[0]`, no picker UI). When/if multi-device discovery lands, the device-picker chunk should ALSO wire its selected `device.id` into `fetchSuggestions(deviceId)` and add the selected device to the `useEffect` dependency array so chips refresh on device switch. The cache key in `backend/api/routes_suggestions.py` is already keyed by `device_id`, so backend is forward-compatible. Source: spawned-task chip from chunk 11 implementation; reviewed and deferred 2026-05-20 — premise required a device-selection UI that doesn't exist yet.
- **Tighten Haiku suggestion grounding** — Phase D chip "Enable OSPF routing protocol on this router" surfaced live even though OSPF process 1 is already configured. The `_build_digest` in `routes_suggestions.py` includes `router ospf 1` lines but Haiku doesn't always treat them as exclusions. Consider an explicit `OSPF: process N active` digest line (similar to how VLANs get `vlan N name X`) so the inner system prompt's "avoid suggesting things already present" rule has a clearer signal. ~15 min when revisited.

## Pre-demo hardening (verified findings from `/review` + `/security-review` on 2026-05-21 at v0.5.5)

Two slash-command reviews ran against `v0.5.5-phase-e-preview-diff`. Both concluded **ship as-is for the alpha-1 demo — nothing attacker-reachable on single-operator localhost** — but flagged ~8 hardening items totalling ~2 hours of work. Each was verified against the actual code on 2026-05-21; corrections noted where the report missed nuance. Pick up as a dedicated "Pre-demo hardening" chunk before cutting `v0.4.0-alpha.1` (chunk 18) or any external sharing.

### MEDIUM — do before any public demo / non-local deployment

- **[SEC-A] `routes_snapshots.py` belt-and-suspenders path containment** — current regex `^act_[A-Za-z0-9_-]+$` + `phase ∈ {pre, post}` literal check is tight, but no `.resolve().is_relative_to(settings.artifacts_dir)` check after path construction. Add the defence-in-depth check around `routes_snapshots.py:47`. ~10 min + 1 test.
- **[SEC-B] Document `_build_digest` allowlist intent** — the `/review` flagged this as a "deny-list" but it's actually already a positive allowlist (only `hostname X` / `vlan N name X` / `interface X ip Y Z` are extracted; `enable secret` / `snmp-server community` / AAA never match). Worth adding an explicit `# SECURITY: This is a POSITIVE ALLOWLIST...` comment in `routes_suggestions.py:69` so future contributors don't accidentally extend it to a wildcard extraction. ~5 min. No tests needed.
- **[QUAL-2] Cache `_verify_running_config` SSH round-trip** — unconditional SSH call per write (`write_tools.py:344`). On Filip's lab adds ~400 ms per write; on chassis with WAN-distance routers could be seconds. Either skip when prior `_check_netmiko_output_for_errors` was clean (config errored = verify-failed already), OR shorten the SSH read timeout from 60 s to 5 s on the success path. ~20 min + adjusted tests.

### LOW — defer or batch with other small wins

- **[QUAL-3] PreviewScreen Back/Approve/Apply buttons console.warn** — `frontend/screen-preview.jsx:123-125` has three placeholder buttons that just log. Either wire them (Back navigates to AI Configuration, Approve+Apply call the same `/api/approve` + `/api/execute` endpoints the AI Config sticky-bar uses) OR delete them so the preview page doesn't have dead controls. ~15 min if wired, ~2 min if deleted.
- **[QUAL/SEC-4 + D] Cache `/api/devices` enrichment** — `routes_devices.py:_enrich_with_show_version` does an SSH `show version` per request. Dashboard + sidebar both poll every few seconds → 6-10 SSH calls/min on idle. Add a 30 s cache (mirror the `routes_suggestions._cache` pattern). ~15 min + 1 test for cache hit/miss.
- **[QUAL-5] Test gap: deeper BGP `address-family ipv4 vrf X` nesting in `_extract_stanza_block`** — existing `tests/unit/test_conflict_detector.py:110-129` covers main `router bgp` + `exit-address-family`. Add one more test for `address-family ipv4 vrf <name>` nested inside the BGP stanza to confirm the walker doesn't terminate early on `exit-address-family`. ~10 min.
- **[SEC-C] Redact raw IOS `%` error lines from chat error events** — `write_tools.py` verify_failed message embeds `device_errors` directly. In prod could leak hostnames / interface names / VRF names via error responses. Either redact `%` lines down to error-category-only ("config rejected — see logs for full text"), OR send the raw text to `log.error` only and surface a sanitised version to chat. ~15 min.
- **[SEC-E] Cap snapshot JSON response size** — `routes_snapshots.py:59` reads full file content unbounded. Lab configs are ~6 KB but chassis switches could be 100+ KB. Add `len(content) > MAX_SNAPSHOT_BYTES` (e.g. 64 KB) → truncate + return `{truncated: true, bytes_total: <N>}` so the frontend can show a "truncated, view on disk" message. ~10 min + 1 test.
- **[SEC-G] Move Anthropic `request_id` from HTTP body to log** — `routes_chat.py:111` (from chunk 10) includes the Anthropic request ID in the 503 detail string. Intentional for support tickets but exposes internal IDs. Could keep `request_id` in `log.warning` and replace the user-facing message with a redacted ID like `req_***` + advice "check server logs for full ID". ~5 min.
- **[QUAL-1 + SEC-F] Documentation: `cli_command_sent` events are intent stream not wire trace** — `_emit_cli_commands` fires BEFORE the SSH `send_config_set` in all 4 write tools (`write_tools.py:380, 475, 562, 344`). The live event stream column shows what the agent INTENDS to send, not confirmation it was sent. Add a one-line doc comment in `_emit_cli_commands` definition and a note in `docs/how-it-works.md`. ~5 min, no behavior change.

### Reviewer's false alarms (verified — no action needed)

- **[QUAL-6] Test gap: `_derive_svi_vlan_id`** — reviewer claimed no tests; actually `tests/unit/test_tool_registry.py:286-295` has two tests (`test_derive_svi_vlan_id_uses_third_octet` + `_falls_back_to_100_for_zero_or_default_vlan`). Coverage exists. No action.

### Out of scope for this pre-demo pass

- **[SEC-H] WebUI driver pre-existing threats** (origin allowlist, sensitive-element deny-list, secret redaction, `propose_webui_configure` scope) from the prior security review window — needs a separate WebUI hardening pass, likely paired with chunk 14b (vision fallback) since both touch `backend/webui_agent/`.

### Recommended bundling

Two parent-chunks before cutting `v0.4.0-alpha.1`:

- **"Pre-demo hardening — MED" chunk**: SEC-A + SEC-B + QUAL-2. ~45 min, three small edits + 2 tests, single commit.
- **"Pre-demo hardening — LOW batch" chunk**: QUAL-3 (delete the dead buttons), QUAL-4/D (devices cache), QUAL-5 (BGP nested test), SEC-C (redact `%`), SEC-E (snapshot cap), SEC-G (request_id log-only), QUAL-1 + SEC-F (intent-stream docs). ~75 min total, batched into one commit per the LOW priority.

Total: ~2 hours, fits before the `v0.4.0-alpha.1` consolidation tag. Or fold piecewise into existing chunks 14, 14b, 15, 17 as opportunistic clean-up.
