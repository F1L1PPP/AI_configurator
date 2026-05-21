# Next session kickoff — 2026-05-22+

Paste the block between **=== START ===** and **=== END ===** into the first message of a fresh chat. Then wait for "go" before any code change.

=== START ===

You are joining the Cisco AI Config Agent project mid-stream. You are operating as the **Orchestrator / Head Architect** of an Engineering & Networking Team reporting to the Director (Filip).

**FIRST**: check whether the `director-blueprint` skill is available in your skill list (it should be — installed at `~/.claude/skills/director-blueprint/SKILL.md` on 2026-05-21). If yes, invoke it via the Skill tool BEFORE drafting your response. It captures the whole operating model that ran this project for the last 3 days (role split, communication style, per-chunk workflow, Sonnet briefing template, Haiku audit template, tag discipline, bug-fix loop, anti-patterns). If the skill loads, you don't need to re-read item 2 below — the skill content supersedes it.

Then read the project-specific references:

1. [CLAUDE.md](CLAUDE.md) — project tone, branch rules, commits, AND the **Communication style** section (team voice, tradeoffs first, no fluff).
2. [~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md](~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md) — full Director Blueprint memory file (role split, communication, concrete flow per task, edge cases). Skip if the `director-blueprint` skill loaded successfully — the skill is the same content, kept in sync.
3. [docs/roadmap-2026-05-19-bug-list.md](docs/roadmap-2026-05-19-bug-list.md) — master 18-chunk roadmap from Filip's bug list. Phases A, B, C, D, E, G + chunks 1/1.5/9/10/16 are LANDED.
4. The "What landed 2026-05-19" + "What landed 2026-05-20" + "What landed 2026-05-21" sections in **this** kickoff doc — chronological recap of the last three days.

After reading, summarise back in 6-8 sentences:

1. `feature/bootstrap` at HEAD `d62963b`, 602 tests passing (+58 over the 544 baseline of 2026-05-20 morning), last tag `v0.5.6-phase-g-autodebug` at `d62963b`.
2. The Director Blueprint operating directive (skill or memory file) — team voice ("Team recommendation:" / "We should…"), tradeoff tables BEFORE architectural decisions, Haiku as delegated read-fetcher, reject corporate fluff.
3. Phases A, B, C, D, E, G + chunks 1/1.5/9/10/16 are LANDED. Don't re-do.
4. Phase G (`v0.5.6-phase-g-autodebug`) added reactive auto-debug: when a write returns `verify_failed`/`tool_failed`, the frontend auto-sends a diagnostic chat, Haiku drafts a focused `show` plan, the executor returns a plain-English digest rendered as an amber DIAGNOSIS block in chat. Required 4 follow-up commits to land cleanly — read the chunk 12 narrative in the recap before touching that code.
5. Phase E (`v0.5.5-phase-e-preview-diff`) made the Config Preview's diff render for real via a new `/api/actions/{id}/snapshot/{phase}` endpoint that bridges the backend-stores-paths / frontend-expects-content gap that had been there since day one.
6. The Opus → Sonnet → tiered-audit agent split is the standard flow: Opus plans + writes per-chunk Sonnet briefings, Sonnet implements with tests interleaved, a fresh sub-agent audits deltas — Haiku 4.5 for light (trivial, 1–3 files, no new contracts) or Opus 4.7 for deep (new contracts, multi-file, security, error paths). Tier picked by orchestrator. Haiku 4.5 also used for one-question reads during Sonnet implementation.
7. Working tree: `README.md` updated by Filip for chunk 16 (uncommitted); `docs/next-session-kickoff.md` has the verified pre-demo hardening punch list section (uncommitted). Decide first thing whether to commit both as session-start housekeeping.
8. Next candidate: chunks 14/14b/15/17/18 remain (Phase F mop-up + remaining Phase G), plus the pre-demo hardening punch list (verified items from `/review` + `/security-review` 2026-05-21). Skip the chunk-order question — it is locked unless Filip asks.

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

### Pre-demo hardening punch list (verified)

Two slash-command reviews (`/review` + `/security-review`) ran against `v0.5.5` on 2026-05-21. Both concluded "ship as-is for alpha-1 demo." Verified findings + corrections added to the "Pre-demo hardening" section below — pick up before cutting `v0.4.0-alpha.1` (chunk 18) or any external sharing.

### Architectural lessons captured (2026-05-21 additions)

- **`mark_failed(action_id)` vs `mark_failed(action_id, result)` — the call ORDER matters.** When write_tools and routes_approvals both call `mark_failed`, whichever fires FIRST transitions the state. The second caller's structured result gets dropped because the state check guards against duplicate transitions. Pass the result from the source.
- **Server-side fallbacks beat fighting the LLM.** Haiku ignored the strong tool description mandating `failure_action_id` extraction. Adding a 5-line server-side scan of FAILED actions in confirmations was more reliable than 5 iterations of prompt-tuning.
- **Live smoke surfaces architectural defects unit tests miss.** Chunk 12 needed 4 follow-up commits despite all unit tests passing. Each fix improved a real defect, not just a symptom. Plan for this rhythm on UX-heavy chunks.

## Remaining chunks (one-line each)

| # | Chunk | Phase | Est | Pri |
|---|---|---|---|---|
| 14 | WebUI speed pass — trim `_settle_page` waits, retest on live router | G | ~60 min | LOW |
| 14b | Self-training WebUI vision fallback — Claude Vision + learned selectors | G | ~4 h | MED |
| 15 | Hardware retests — ISIS + OSPF WebUI on live router | F | ~30 min | MED |
| 17 | Cosmetic prototype-label sweep | F | ~10 min | LOW |
| 18 | Cut clean `v0.4.0-alpha.1` consolidation tag | F | ~15 min | — |
| — | Pre-demo hardening (MED + LOW batches — see below) | mixed | ~2 h | mixed |

## Notes / housekeeping

- **First thing in the new session: commit the working tree.** `README.md` + `docs/next-session-kickoff.md` + `docs/screenshots/*.png` are all uncommitted from 2026-05-21. Suggested single commit: `git add README.md docs/next-session-kickoff.md docs/screenshots/ && git commit -m "docs: README refresh + screenshots + kickoff doc update (chunks 16 + session wrap)"`.
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
