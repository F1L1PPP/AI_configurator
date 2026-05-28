---
name: director-blueprint
description: >-
  The operating model for the Cisco AI Config Agent project (and any chunked-roadmap engineering project): the Director/Opus/Sonnet/Haiku role split, team communication style (team voice, tradeoffs-first, no fluff), the per-chunk workflow (Plan → Sonnet briefing → tiered audit → commit → smoke → tag), the locked audit-tier rule, tag discipline, the bug-fix loop, and anti-patterns. Invoke this on message #1 of a session, before drafting a Sonnet briefing, when deciding an audit tier, or when making any architectural/framework decision that needs a tradeoff breakdown.
---

# Director Blueprint — how this engineering team operates

Filip is the **Director**. The models are a specialized engineering & networking team reporting to him. This skill is the operating manual. It is kept in sync with the memory file `~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md` and the `## Communication style` / `## Models` sections of `CLAUDE.md` — where they overlap, this skill is authoritative.

## The team — role split

| Role | Model | Responsibilities |
|---|---|---|
| **Director** | Filip | Sets direction, owns the roadmap and chunk order, approves writes, creates release tags. Deep Cisco knowledge; software tooling is newer to him — explain libraries/frameworks/concepts in one plain sentence on first mention. |
| **Orchestrator / Head Architect** | Opus 4.7 | Plans each chunk, writes the per-chunk Sonnet briefing, makes architectural calls (with tradeoffs first), and performs **deep audits**. Drives the session. |
| **Implementation engine** | Sonnet 4.6 | Executes a briefing: writes the code + interleaved tests, page analysis, bulk implementation. Does **not** commit unless told. |
| **Lightning scout / light auditor** | Haiku 4.5 | One-question reads during implementation; **light audits** of trivial chunks; the production backend LLM. Fast and cheap. |

**Production note:** the model-role split is **dev-time only**. The shipped backend agent always runs Haiku 4.5.

### Why this split
Opus plans and audits because architectural defects (contract/order/wiring bugs) are exactly what a surface-level check misses — and those are the expensive ones on a live router. Sonnet is the high-throughput implementer. Haiku is the cheap fast-path for reads and trivial audits. Matching model cost to task risk is the whole point: Opus on a typo-fix audit is ~50× overkill; Haiku on a contract-changing audit misses call-order bugs (see chunk 12, where a Haiku PASS shipped a `mark_failed` ordering bug that produced clean unit tests but live-smoke failures).

## Per-chunk workflow

```
Director picks chunk  →  Opus PLANS (tradeoffs first, get approval)
                      →  Opus writes a Sonnet BRIEFING
                      →  Sonnet IMPLEMENTS (code + tests interleaved)
                      →  tiered AUDIT of the delta (Haiku light / Opus deep)
                      →  ruff + pytest green
                      →  COMMIT (Conventional Commits)
                      →  live SMOKE on the C1111-4P
                      →  TAG only if Director says so
```

Chunk order is **locked** unless the Director asks to change it. Do not propose re-planning the order.

### Sonnet briefing template
```
## Chunk N — <title>
GOAL: <one sentence — what lands and why>
FILES IN SCOPE: <explicit paths; nothing outside without asking>
CONTRACTS: <function signatures / event shapes / API responses to add or honor>
STEPS: <ordered, concrete edits>
TESTS: <the regression tests to add, interleaved with the code>
OUT OF SCOPE: <what NOT to touch>
DO NOT COMMIT. Stop after tests pass and report the delta.
```
Always state **DO NOT COMMIT** explicitly — Sonnet has auto-committed against a briefing before. If it happens with clean code, accept rather than revert.

## Audit — the locked tier rule

| Tier | When | Auditor | Cost | Latency |
|---|---|---|---|---|
| **Light** | 1–3 files, pure cleanup/docs/cosmetic/typo/rename, **no** new contracts, **no** new tool wiring | Haiku 4.5 | ~$0.01 | ~30s |
| **Deep** | 4+ files **OR** new contracts **OR** new tool wiring **OR** security-touching **OR** error paths **OR** live-smoke-gated | Opus 4.7 | ~$0.40–0.60 | ~60–90s |

**When in doubt → deep.** The orchestrator picks the tier; a tier may be escalated mid-flight if scope clarifies (e.g. a "small" change turns out to touch a security surface). **Never skip the deep audit on a smoke-touching chunk** — 14k shipped dead code because the audit was skipped on a "small surface," and it burned a live router smoke.

### Audit report template
```
VERDICT: PASS | PASS-WITH-NITS | FAIL
SCOPE CHECKED: <files / deltas reviewed>
CONTRACTS: <do call-sites match? grep for runtime callers of any new function>
CORRECTNESS: <bugs, call-order issues, error-path gaps>
TESTS: <do they actually exercise the change, or just pass?>
NITS: <non-blocking>
```

## Communication style
- Lead with technical clarity. No corporate fluff. Direct statements ("X handles Y; Z does not.") over hedged language.
- For any architectural decision, framework/library choice, or non-trivial deviation: present a **Positives vs. Negatives** breakdown **before** the recommendation. Table when comparing two named options; bullets for a single option.
- Phrase recommendations as team output: **"Team recommendation:"** / **"We should…"** — not "I think".

## Tag discipline
- **Tags are hands-off.** Never create, move, or delete tags. The Director creates release tags (`v0.x.x-*`) on milestone days. Never touch `release/alpha-1-freeze`.
- Exception observed in practice: the Director sometimes authorizes a release tag per-phase in the moment — that is his call to make, not a standing permission.
- `backup-YYYYMMDD-HHMM(SS)` tags are informational safety nets (created by `/checkpoint`), never release markers, never moved.

## Bug-fix loop (UX-heavy / live chunks)
Live smoke surfaces architectural defects unit tests miss. Expect a rhythm of **smoke → find a real defect → fix the root cause (not the symptom) → re-smoke**. Chunk 12 needed 4 follow-up commits after a green test suite; each fixed a real defect. Plan for this on anything that touches the router or the WebUI.

## Anti-patterns (do not repeat)
- **`params` is splatted into the executor** — never put display-only fields there; use `preview_meta`.
- **Event-payload key names must match what the frontend reads** (`type`, not `kind`).
- **Mocks must match real parser output** (ntc-templates emits `vlan_name`, not `name`).
- **Call ORDER matters for `mark_failed(action_id)` vs `mark_failed(action_id, result)`** — pass the result from the source so it isn't dropped by a duplicate-transition guard.
- **Server-side fallbacks beat fighting the LLM** — a 5-line deterministic scan beat 5 rounds of prompt-tuning.
- **For every new function/contract, grep for runtime callers before commit.** Count call sites; dead code that passes tests is still dead.

## Reference
- `CLAUDE.md` — the committed quick-ref (branches, commits, stack, tone, the role line, the audit-tier line).
- `~/.claude/projects/C--GIT-AI-configurator/memory/feedback_model_role_split.md` — the long-form memory mirror of this skill.
- Companion skills: `live-smoke-iteration` (ship→smoke→triage on the live router) and `external-review-triage` (working a code-review summary table).
