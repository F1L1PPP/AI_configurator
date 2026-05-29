---
name: external-review-triage
description: >-
  Workflow for processing a multi-row external code-review summary table (e.g. a PM or third-party reviewer drops a 10–15 item findings table). Verify each finding independently via a Haiku Explore agent (real / misread / already-fixed), bundle the real ones by severity into chunks, ship each chunk with a deep audit, update the kickoff doc, and propose a tag. Invoke this whenever a review summary table or numbered list of findings appears in the conversation.
---

# External review triage

When a reviewer hands over a summary table of findings, **do not start fixing top-to-bottom.** Reviews mix real bugs with misreads and already-fixed items, and severity is the reviewer's guess, not ground truth. Triage first.

## Workflow

```
1. VERIFY each finding via a Haiku Explore agent  →  real | misread | already-fixed
2. BUNDLE the real findings by severity            →  chunks (CRITICAL alone, then HIGH, MEDIUM, LOW batch)
3. SHIP each chunk: implement → tests → deep audit → commit
4. UPDATE the kickoff doc (what landed, deferred, misread)
5. PROPOSE a tag to the Director (never self-tag)
```

### 1. Verify — one Haiku Explore agent per finding
Send each finding to a Haiku Explore agent with the exact file/line the reviewer cited and the claim. Classify:
- **Real** — the code confirms the issue. Keep it.
- **Misread** — reviewer misunderstood the code (e.g. "validators duplicated" when they're centralized and imported; "static mount shadows routes" when it's already last with a `keep this LAST` comment).
- **Already-fixed** — a prior commit covers it (e.g. a test the reviewer says is missing already exists).

Record the count. A typical pass is roughly two-thirds real (the 2026-05-21 pass: **11 real of 15**, 4 misread/already-fixed).

### 2. Bundle by severity
- **CRITICAL** → its own chunk, shipped first.
- **HIGH** → grouped (1–2 per chunk).
- **MEDIUM** → grouped.
- **LOW** → a single batch commit.
Each chunk is one focused commit with its own tests.

### 3. Ship with a deep audit per chunk
Anything review-driven is touching contracts, security surfaces, or error paths almost by definition — so default to an **Opus 4.8 deep audit** (per `director-blueprint`'s tier rule). Escalate mid-flight if a chunk's scope turns out to touch a security surface (the WS strict-origin item escalated chunk D). Run `ruff` + `pytest` green before each commit.

### 4 & 5. Document and propose a tag
Update the kickoff doc with three lists — **landed**, **deferred** (with the reason and the follow-up it's tracked under), **misread/already-fixed** (so no one re-investigates). Then propose a release tag to the Director. **Never create the tag yourself.**

## Worked example — 2026-05-21 pass (15-item table → 4 chunks, +21 tests)

| Chunk | Findings | Tier | Tests |
|---|---|---|---|
| **A** | #1 Chromium sessions never closed (CRITICAL) | Deep | +2 (604) |
| **B** | #3 Empty cred defaults + #2 Action-store TTL (HIGH×2) | Deep | +8 (612) |
| **C** | #5 TOCTOU race + #7 Param signature guard + #6 mypy cleanup (MEDIUM×3) | Deep | +6 (618) |
| **D** | #9 WebUI goto timeout + #11 Eventbus log throttle + #14 WS strict-origin (LOW×3) | Deep (escalated) | +5 (623) |

Deferred: #8 name-based redaction → `SecretStr` migration (too large for the MEDIUM batch). Misread/already-fixed: #4, #10, #12, #13, #15.

## Recurring anti-patterns this project's reviews surface
Check new code against these eight — they have all appeared as real findings:
1. **Blanket cleanup on a request-scoped `finally`** for a resource that spans turns — clean up based on whether the *work* is finished, not whether the request is exiting.
2. **Two-step `get_state` then act** — a TOCTOU race; use an atomic compare-and-set primitive.
3. **`params` splat** carrying display-only fields into the executor — use `preview_meta`.
4. **Name-based secret redaction** that misses renamed fields — prefer `SecretStr`.
5. **Empty credential defaults** that let the app boot misconfigured — fail at boot with the missing creds listed.
6. **Static-mount route shadowing** — keep the static mount last.
7. **WebSocket missing-origin bypass** — an absent `Origin` header must not skip the allowlist.
8. **Eventbus log flood** — throttle backpressure warnings (one per interval, aggregate the drop count).

## Reference
- `director-blueprint` — the audit-tier rule and overall workflow this slots into.
- `docs/next-session-kickoff.md` — where the landed/deferred/misread lists get recorded.
