# 2026-06-05 — WebUI driver rebuilt: Atlas + Accessibility-Tree. OSPF + DHCP GREEN.

## Headline
The slow/broken Playwright-screenshot "vision-hybrid" WebUI configurator was **replaced** with a
new **Atlas + DOM-keyed perception** driver. As of tonight, **two distinct settings sections —
OSPF and DHCP — configure end-to-end through the SAME generic engine** (perceive → plan → fill →
Apply → verify → snapshot). Nothing is hard-coded per section. 12 commits on `feature/bootstrap`,
**1155 unit tests green**, ruff + mypy clean. Pushed to GitHub.

## Why / what changed
The old driver was slow (>5 min, ~1 min just to click Apply), inaccurate (couldn't fill OSPF/DHCP),
and re-planned at execute time (`inner_plan_empty`). New design (approved plan
`~/.claude/plans/i-dont-like-the-tidy-newell.md`):
- **Perceive** = ONE batched `page.evaluate` DOM extraction (no per-element round-trips, no
  screenshots, no `networkidle`), fields keyed by their stable DOM identity (`name`/`ng-model`).
- **Plan once** = `draft_atlas_plan` (Haiku 4.5) emits typed `{field_key,value}`, validated against
  the live fields; the approved plan is EXACTLY what runs at execute (no LLM re-plan → kills
  `inner_plan_empty`).
- **Act** = typed widget adapters (input / Kendo combobox / checkbox / radio / button) located by
  stable selectors, narrowed to the visible element; per-field read-back self-verify.
- **Verify** = a11y/text scan for a planner-chosen identifier (+ CLI readback); pre/post snapshots.
- Vision demoted to a last-resort rung; all the HITL approval / deny-list / apply-never-retry
  safety preserved.

## Commit arc (feature/bootstrap)
- **A `3326ec0`** atlas foundation (schema/fingerprint/store/reconcile)
- **B `226d762`** a11y/DOM capture + perceive
- **C1 `5f9929f`** widget adapters · **C2 `21c9e49`** typed planner + validation
- **C3 `8378409`** act-path session ops (act_field / apply_control / verify_a11y, self-heal)
- **C4 `2bf14a2`** wired the live chat to the atlas path (dispatch switch; legacy kept as fallback)
- **C5 `5135b4a`** DOM-keyed perception — capture REAL fields not junk (inner_text, stable identity)
- **C6 `d020b27`** resolve duplicate-name fields to the VISIBLE element (Cisco Basic/Advanced)
- **C7 `ac59264`** robust apply-button locating (lenient role + text fallback)
- **C8 `964e4c5`** general cross-section robustness — **7-Opus-agent workflow** (open-form gate fix
  = the DHCP blocker; grid-junk filter; ng-model identity; label quality; verify/snapshot/visibility)
- **C9 `1986e4f`** Kendo combobox label-by-value fix + idempotent skip (the last DHCP blocker)

## The live-smoke-iteration (what each smoke taught us)
Each smoke peeled one layer; every fix was caught by Opus audit, not the tests:
1. OSPF: capture mislabeled junk ("17.6.3a") → **C5** DOM-keyed identity.
2. OSPF: `[name='processID']` matched 4 elements → strict-mode → **C6** visible-narrowing.
3. OSPF: Apply button `get_by_role(exact=True)` missed (save icon) → **C7** lenient role.
4. OSPF GREEN. DHCP fell to CLI: stray list checkbox broke the open-form gate → **C8** `not _has_submit` gate.
5. DHCP: Kendo Subnet Mask labeled/keyed by its value → `element_intercepted` → **C9** inner_text-≠-label + idempotent skip.
6. **OSPF + DHCP GREEN.**

## Evidence
- OSPF: `router ospf 100` / `router-id 10.10.10.1` landed. DHCP: pool MYPOOL / network 20.20.20.0/24 landed (`act_20260605_836af3`, snapshots saved).
- Atlases auto-built at `webui_atlas/c1111-4p__17-6-3a/routes/{ospf,dhcp}.json` (gitignored).
- Findings + deferred items: `docs/smoke-findings-20260605.md`.

## Known follow-ups (NOT done — pick from these tomorrow)
- **More sections** — re-smoke static routes, VLAN, ACL, interfaces through the same engine to prove breadth.
- **FEAT-SMART** (`docs/smoke-findings-20260605.md`) — capability-aware clarify + Advanced-tab discovery/suggest (e.g. DHCP default-gateway / OSPF area live on Advanced; agent should detect + offer).
- **Kendo WRITE path** — setting a NON-default Kendo value (different mask, IPv6) still uses the fragile open→click; harden (reorder to hidden-select, or scope the popup). Today's idempotent-skip only covers already-correct values.
- **Kendo backing-select resolution bug** — the Lease "Never Expires" combobox captured `kendo_select_name='ipTypeList'` (wrong; the 6-level walk grabbed a sibling's select). Fix the `kendoSelectName` JS scope.
- **Label cosmetics** — a few labels still read oddly; display-only, non-blocking.
- **CLI-agent bugs F1–F5** (`docs/smoke-findings-20260605.md`) — `set_interface_ip` blindly prepends `no switchport` (breaks routed Gi0/0/x); FAILED-state sticky-bar buttons; desc≠commands; perf. Fix-later.
- **Cleanup** — remove the legacy `_propose_webui_configure`/`_webui_configure` + dead semantic_dom helpers once confident; 15 skipped legacy tests are the revert reference.
- **Merge path** — `feature/bootstrap` → `develop` → `main` when ready (Director's call). Tag is Filip's call.
