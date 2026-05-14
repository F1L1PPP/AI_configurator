# Plan — AI-First WebUI Configuration (v0.4.0)

## Context

Today (2026-05-14) we proved the full WebUI write path end-to-end: VLAN 46 'IOT'
configured via the Cisco WebUI, verified by CLI, snapshot evidence captured.
Subprocess isolation solved the Windows asyncio Catch-22, the WebUI manual is
in RAG (913 chunks), and the friendly error catalog renders properly. 296 tests
green; ruff / mypy / tsc clean.

**What changes now:** instead of hand-coding a Page Object Model for every
single feature in the Cisco IOS XE WebUI (50+ features in the manual), we let
the AI drive the WebUI itself. The agent reads the manual via RAG, looks at
the current page semantically, and decides which element to click. Same safety
gate as today (approve → snapshot → write → verify → mark), but the *content*
of the configuration step is AI-decided, not hand-coded.

**Why this shift:** Filip's framing — "what we did until today was training to
see if I can script it; now I want to try the AI flow with the manual and
visual intelligence." The codebase today is the proof that the safety rails,
subprocess isolation, evidence trail, RAG, and approval gate all work. Now we
keep that scaffolding and replace the brittle hand-coded selectors with an AI
that picks elements by semantic intent.

**The cost constraint:** "must not consume that much of credits." Treated as
a first-class requirement, not an afterthought. The cost-discipline phase
runs **before** the agentic phases.

---

## Workstream 1 — Manual into RAG: ✅ DONE

Cisco IOS XE Web UI User Guide PDF dropped into `knowledge_base/docs/`,
ingested via the existing pipeline. 913 chunks total across 3 PDFs. RAG
retrievals confirmed (e.g. "create OSPF route in WebUI" → top hit
`cisco_guide_for_the_web_ui.pdf — Configuring OSPF Routing`, cosine 0.564).
Sources panel in chat already renders citations. No further work needed.

---

## Workstream 2 — Generic AI-Driven WebUI Configuration

The main work. Seven phases, sequenced so cost discipline lands before any
LLM call grows in size.

### Phase 0 — Mark the milestone + backup (gate, ~10 min)

Filip's explicit ask: "mark this version that it works, also do backup before
that big change."

1. `git tag -a v0.3.2-webui-flows-working <HEAD> -m "First successful WebUI VLAN add end-to-end. Subprocess isolation + verifier fix + RAG manual."`
   HEAD at write time: `b084088`. Per CLAUDE.md, milestone tags require Filip's
   explicit go — granted here.
2. `git tag -a backup-20260514-HHMM HEAD -m "Pre-AI-first-shift snapshot."`
   This is the per-CLAUDE.md daily backup tag, autonomous.
3. `git push origin v0.3.2-webui-flows-working backup-20260514-HHMM`

Nothing else changes in this phase. The tags are the rollback point if the
AI-first work goes sideways.

### Phase 1 — Cost discipline (foundation, ~1 day)

Three changes to the planner before any new tool is added. Without these, the
AI-driven phases would balloon token usage with the bigger tool schema +
per-step describe_page snapshots.

**1a. Prompt caching** ([backend/orchestration/planner.py:38–132, 207–327](backend/orchestration/planner.py))

Today the system prompt (~2 KB) and all 15 tool schemas (~4 KB) are
re-transmitted on every iteration. Adding `cache_control: {"type": "ephemeral"}`
on the last block of the system prompt + on the last tool in the tool list
caches both. Cache hits cost ~10% of fresh tokens. Estimated saving: 15–20%
input tokens for typical multi-iteration flows.

Reference: Anthropic's prompt-caching docs. The exact wiring sits inside the
`messages.create` call (`planner.py` around line 237).

**1b. Token telemetry** ([backend/orchestration/planner.py:237](backend/orchestration/planner.py))

The Anthropic SDK populates `response.usage.{input_tokens, output_tokens,
cache_creation_input_tokens, cache_read_input_tokens}` automatically — we just
don't log it. Add one `log.info("planner_iteration_usage", iteration=i,
input_tokens=..., output_tokens=..., cache_read=..., cache_creation=...)`
right after every `messages.create` return. Backend logs only (no UI surface
yet — keep it simple).

This is the cost-visibility gap. Without it, we can't tell whether the new
AI-first flows cost $0.02 each or $0.20 each.

**1c. RAG cap** ([backend/knowledge_agent/retrieve.py:66](backend/knowledge_agent/retrieve.py), [backend/core/settings.py:47](backend/core/settings.py))

Today `rag_top_k=5`, no per-chunk truncation, worst case ~1 250 tokens per
search_docs call. For WebUI-flow queries we typically need ONE good section,
not five. Add a `top_k` override on the agent's `search_docs` tool call
(allow the planner to pass `top_k=3` for narrow lookups) and document
`top_k=3` as the new default in the system prompt.

### Phase 2 — Decide what stays, what moves to history

| Component | Decision | Why |
|---|---|---|
| `backend/orchestration/confirmations.py` (approval state machine) | **KEEP** | The safety gate. Unchanged. |
| `backend/cli_agent/snapshots.py` (pre/post snapshots) | **KEEP** | Per-CLAUDE.md mandatory before every write. |
| `backend/webui_agent/_subprocess.py` + `_playwright_subprocess.py` (subprocess isolation) | **KEEP** | Just shipped today, solves the Windows asyncio Catch-22. AI-first builds on top of this. |
| `backend/webui_agent/evidence.py` (screenshots + DOM dumps) | **KEEP** | Audit trail. AI-first reuses it. |
| `backend/webui_agent/login.py` + `selectors/` (login flow + first_match strategy) | **KEEP** | Login is stable; the new generic driver wraps the rest. |
| `backend/cli_agent/write_tools.py` (CLI fast paths: set_hostname, set_interface_ip, set_access_vlan) | **KEEP** | These are the "few scripts" Filip wants alongside the AI. CLI is 10× cheaper than AI-driven WebUI for the same operation. |
| `backend/webui_agent/flows/change_hostname.py` + `add_access_vlan.py` (WebUI flow wrappers) | **KEEP as fast paths** | These just shipped working today. Keep as cheap fast paths for the two most-used WebUI operations. The new generic configure is the fallback for anything else. |
| `backend/webui_agent/pages/hostname_page.py` + `vlan_page.py` (Page Object Model) | **KEEP** (for now) | Backs the fast-path WebUI flows above. If we later drop the fast paths, archive these to `backend/webui_agent/pages/_archive/`. |
| Validators in `backend/cli_agent/write_tools.py` (_validate_hostname, _validate_ipv4, _validate_vlan_*) | **KEEP** | Defense-in-depth for the CLI path. AI-first does its own validation, but these stay for the fast paths. |
| Hand-coded scenario pages in `frontend/app/actions/*` | **KEEP** | Three forms for hostname / interface-IP / VLAN-add. Fast-path UX. |
| Reviewer-prompt artifact (formerly bottom of this plan file) | **REMOVED** | One-shot artifact from earlier today, already used to generate the code review. |

The principle: the safety, audit, and fast-path infrastructure stays. What
goes away is **the assumption that every new feature needs a hand-coded page
object**. New features now route through the AI-driven generic.

### Phase 3 — Semantic DOM driver (~1–2 days)

New module: `backend/webui_agent/semantic_dom.py`

Single function `describe_page(page) -> dict` that walks the current Playwright
Page and returns:

```json
{
  "url": "/Routing/OSPF",
  "title": "OSPF Routing",
  "elements": [
    {"eid": "e_001", "role": "button", "name": "Add", "visible": true, "enabled": true, "bbox": [820, 240, 60, 32]},
    {"eid": "e_002", "role": "combobox", "name": "Router", "value": "OSPF"},
    {"eid": "e_003", "role": "textbox", "name": "Process ID", "value": "", "required": true}
  ],
  "modals": [...],
  "errors": [...]
}
```

- `role` from ARIA. `name` from aria-label → labelled-by → text → placeholder.
- `eid` is a stable per-call id; an internal dict maps `eid → Playwright locator`.
- **Token discipline**: cap at top 30 elements by visibility + centrality.
  Hidden / non-interactive elements omitted. Estimated ~500–800 tokens per
  call, not the raw HTML which would be 50× that.
- `modals` flagged separately so the AI deals with dialogs before resuming.
- `errors` from role=`alert` — surfaces validation errors immediately.

Unit tests: snapshot tests against canned HTML in `tests/unit/test_semantic_dom.py`
(no real browser needed; mock `page.query_selector_all` etc.).

### Phase 4 — Resilient action tools + self-heal (~1 day)

New module: `backend/webui_agent/generic_driver.py`

Tools exposed to the planner:

- `webui_open(path)` — navigate. Returns the post-navigation `describe_page`.
- `webui_describe_page()` — fresh snapshot of the current page.
- `webui_act(eid, action, value=None)` — `action ∈ {click, fill, select, check, hover}`. Uses the locator map from the most-recent describe.
- `webui_act_by_intent(intent)` — convenience one-shot: AI says "click the Save button below the OSPF form", the driver picks the best `eid` from current describe by role+name fuzzy match (deterministic local match, no extra LLM call), acts on it, returns the result + a refreshed describe. Saves a planner round-trip.
- `webui_verify(text_present)` — post-condition check (text exists on page).

**Self-heal loop**: when `webui_act` fails (locator timeout, element not in DOM,
click intercepted, disabled), do NOT throw. Instead:

1. Detect failure mode (intercepted vs missing vs disabled vs hidden).
2. Re-describe the page automatically.
3. Return the new snapshot to the planner with `failure_reason` annotated.
4. Bounded retries: max 2 self-heal attempts per step. After that, fall to
   Phase 6 (vision) or surface the failure.

Common modes handled by default: modal/tooltip dismissal, element re-rendered
with new internal id (re-match by role+name), element disabled (look for a
parent "Edit" button), iframe boundary switching.

Per CLAUDE.md: "never auto-retry on writes" — that rule applies to the
*router-side* config write. Re-trying which DOM element to click is upstream
of the write and is fine.

### Phase 5 — The high-level configure tool (~1 day)

New planner tool: `propose_webui_configure(intent: str)`

Flow:

1. AI calls `search_docs(intent, top_k=3)` → manual section + click-path.
2. AI calls `webui_open(path)` and reads the `describe_page` result.
3. AI drafts a step plan: `[{action: click, target: "Add"}, {action: fill, target: "Process ID", value: "100"}, ...]`.
4. Plan + risk assessment returned → Filip sees inline APPROVE buttons.
5. On APPROVE: planner runs each step via `webui_act_by_intent` with self-heal.
   Screenshot at every step. Hard stop on any unrecoverable failure.

**Same HITL gate**, **same evidence trail**, **same approval state machine** as
today's hand-coded flows. The difference is the SOURCE of the step plan: AI
+ manual, not hand-coded selectors.

Update [backend/orchestration/planner.py](backend/orchestration/planner.py) system
prompt: add a section explaining when to use the generic configure path vs.
the fast-path CLI/WebUI tools. Rule of thumb: hostname / interface IP / VLAN
→ use fast path. Anything else → generic configure.

### Phase 6 — Vision on-demand (~0.5 day)

Vision is **only-on-demand**, not always-on. Cost: ~1 500 tokens per image at
Haiku 4.5 rates = ~$0.0015 per call. Aim: <5% of flows trigger it.

New tool: `webui_visual_check(question)` exposed to the planner. Implementation:
take a fresh screenshot, overlay numbered bounding boxes on each element from
the current `describe_page`, pass image + question to Claude (`messages.create`
with an image content block).

Wired into:
- The self-heal loop after DOM-semantic fails twice on the same step
- An explicit AI-initiated call when the planner judges visual confirmation
  is warranted (e.g. "did the save banner actually appear?")

Default off. Off-budget. The DOM-semantic path handles >95% of Cisco WebUI
based on the manual's structure. Vision is the safety net for icon-only
buttons / unlabelled elements.

### Phase 7 — Archive what's unused (~0.5 day)

After the AI-driven generic proves out on at least one real-router flow
(OSPF route is the canonical test from the existing plan), survey:

- Any page-object methods in [backend/webui_agent/pages/](backend/webui_agent/pages/)
  that aren't called by the fast-path flows? If yes, archive to
  `backend/webui_agent/pages/_archive/` with a README.
- Selector YAML entries in `backend/webui_agent/selectors/` for features the
  fast paths don't use? Archive.
- Dead test files under `tests/unit/test_webui_*.py` for archived pages?
  Archive alongside.

Do NOT delete — archive. The fast paths still depend on `HostnamePage` /
`VlanPage`, and a future regression might want to reference an old selector
strategy.

---

## Workstream 3 — Fast paths: scope shrunk to "keep what we have"

Previously planned to add ~21 new hand-coded fast paths (Interfaces, VLANs,
Routing). **Cancelled** by the AI-first shift: the only operations worth a
fast path are the ones we already have (hostname, interface IP, VLAN add) —
they're high-frequency and CLI-fast. Everything else routes through
`propose_webui_configure`.

If a specific operation turns out to be performance-critical AND cost-sensitive
AND used >10× per day, revisit on a case-by-case basis. Until then, no new
hand-coded actions.

---

## Workstream 4 — UI redesign: still deferred

Unchanged from the previous plan. Mockups exist for Chat / Action Library /
Preview Change. Sidebar regrouping (OPERATIONS / AUTOMATION / INTELLIGENCE /
EXECUTION / DATA & HISTORY / ADMIN) is a small additive change that doesn't
block anything; everything else waits for the rest of the mockups.

`/preview` already removed from sidebar nav today; lives on as a deep-link page
until it's rehydrated with real data.

---

## Sequencing

| When | What | Touch points |
|---|---|---|
| Now (~10 min) | Phase 0: tag `v0.3.2-webui-flows-working` + daily backup | Repo tags only |
| Day 8 morning | Phase 1: prompt caching + token telemetry + RAG cap | [backend/orchestration/planner.py](backend/orchestration/planner.py), [backend/knowledge_agent/retrieve.py](backend/knowledge_agent/retrieve.py), [backend/core/settings.py](backend/core/settings.py) |
| Day 8 afternoon | Phase 2: keep/archive matrix lands (no code change yet, just commit a README in `backend/webui_agent/` documenting the architecture) | Docs only |
| Day 9 | Phase 3: `semantic_dom.py` + unit tests on canned HTML | New module + test file |
| Day 10 | Phase 4: `generic_driver.py` + self-heal loop. Validate against the test fake-WebUI before touching the real router. | New module + test file |
| Day 11 | Phase 5: `propose_webui_configure` tool + planner system prompt update. First real-router flow: OSPF route. | [backend/orchestration/tool_registry.py](backend/orchestration/tool_registry.py), [backend/orchestration/planner.py](backend/orchestration/planner.py) |
| Day 12 | Phase 6: vision-on-demand tool. Confirm <5% trigger rate on real flows. | `backend/webui_agent/vision.py` (new), planner update |
| Day 13 | Phase 7: archive sweep + tag `v0.4.0-ai-first` | Code housekeeping + tag |

---

## Verification (end-to-end)

After the AI-first path lands:

1. **Cost regression check**: pick three flows (read-only chat, fast-path
   VLAN add, generic OSPF configure). Compare `planner_iteration_usage` logs
   to a baseline captured before Phase 1. Expect: fast paths unchanged or
   cheaper (caching), generic flow ~20–30k tokens uncached / ~12–18k tokens
   cached → ~$0.012–$0.025 per flow at Haiku 4.5 pricing.
2. **Cabled-session smoke**: chat → "Configure OSPF process 100 area 0 with
   network 10.0.0.0/8 on LAB-R1 via WebUI." Expected: AI retrieves manual
   section, calls `webui_open` + `describe_page`, drafts plan, inline APPROVE,
   step-by-step screenshots, `show running-config | section ospf` confirms.
3. **Self-heal smoke**: same flow, but hand-pop a confirmation modal mid-flow.
   Expected: AI detects, dismisses modal, resumes. No human intervention.
4. **Vision-fallback smoke**: stage an unlabelled icon-only button scenario
   (mock if needed). Expected: AI's DOM path fails twice, vision tool fires,
   correct element gets clicked. Token telemetry shows vision call cost
   logged separately.
5. **Token-budget assertion**: a new unit test that runs the planner with a
   stubbed Anthropic client and asserts `cache_read_input_tokens > 0` on the
   second iteration of a multi-step flow. Catches a future caching regression.

All of the above run as smoke scenarios — gated on `ROUTER_HOST` in `.env` for
items 2 / 3 / 4; item 1 + 5 run without a router.

---

## Files Likely to Change / Be Created

**Phase 1 (cost discipline):**
- [backend/orchestration/planner.py](backend/orchestration/planner.py) — add `cache_control` on system prompt + last tool, log `response.usage`
- [backend/knowledge_agent/retrieve.py](backend/knowledge_agent/retrieve.py) — allow `top_k` override
- Test: new `tests/unit/test_planner_caching.py` — assert cache markers + usage telemetry are emitted

**Phase 3:**
- New: `backend/webui_agent/semantic_dom.py` — `describe_page(page)`
- Test: `tests/unit/test_semantic_dom.py`

**Phase 4:**
- New: `backend/webui_agent/generic_driver.py` — `webui_act`, `webui_act_by_intent`, self-heal loop
- Test: `tests/unit/test_generic_driver.py`

**Phase 5:**
- [backend/orchestration/tool_registry.py](backend/orchestration/tool_registry.py) — add `webui_*` tool family + `propose_webui_configure`
- [backend/orchestration/planner.py](backend/orchestration/planner.py) — system prompt update for tool-selection rules
- Test: `tests/smoke/scenarios/test_07_generic_webui_configure.py` (real router, OSPF)

**Phase 6:**
- New: `backend/webui_agent/vision.py` — `webui_visual_check(question)` with image content block
- [backend/orchestration/tool_registry.py](backend/orchestration/tool_registry.py) — expose `webui_visual_check`
- Test: mocked-vision unit test confirming bbox overlay generation

**Phase 7:**
- Archive moves under `backend/webui_agent/pages/_archive/` + README
- New tag `v0.4.0-ai-first`

---

## Open Questions

1. **Caching TTL**: 5-minute default is fine for a single flow, but Anthropic
   also offers 1-hour caching at higher write cost. Worth evaluating after
   Phase 1 ships and we have real numbers.
2. **History truncation**: not in scope today. If chat sessions get long and
   token bills climb despite caching, add a 20-turn cap (drop oldest pre-cache).
3. **Vision model choice**: Haiku 4.5 supports vision. Sonnet 4.6 is more
   accurate but ~5× the cost. Default to Haiku 4.5; only escalate to Sonnet
   if vision is misreading the page.
4. **Multi-device**: still single-device (LAB-R1). The generic configure
   path works for any device that runs the same IOS XE WebUI, but UI for
   device selection is Workstream 4 territory.

---

## Constraints (carry-over from CLAUDE.md, restated)

- Locked stack: Python 3.12, FastAPI, Pydantic, Netmiko, Playwright (sync),
  ChromaDB, sentence-transformers MiniLM, Anthropic SDK (no LangChain).
  Next.js 14 + TS + Tailwind frontend.
- Never auto-retry on writes (the router-side write — self-heal on DOM
  selection is fine).
- Pre-write device snapshot mandatory. `artifacts/device-snapshots/<action_id>/`.
- Every router write goes through the approval gate. No prompt override.
- Conventional Commits. `ruff check` + relevant pytest + `mypy` before every
  commit. Push after every green run.
- Tags hands-off **except**: `v0.3.2-webui-flows-working` (this plan's Phase 0,
  authorised) + `v0.4.0-ai-first` (Phase 7, will need authorisation when we get
  there) + daily `backup-*` tags (autonomous per CLAUDE.md).
