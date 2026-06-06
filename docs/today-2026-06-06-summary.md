# 2026-06-06 — Code-review remediation, dead-code sweep, CI green, + the Kendo dropdown saga

Full session record. Work happened in the canonical worktree
`C:/GIT/AI_configurator/.claude/worktrees/loving-villani-1fe4d5` on branch
**`feature/bootstrap`** (the repo-root `backend/` has NO source — see §2). Venv:
`C:/GIT/AI_configurator/.venv/Scripts/python.exe`.

---

## TL;DR
- Ran `/review` + `/security-review` (multi-agent, adversarially verified), then shipped **4 fix chunks (A–D)** + a **radio value-selector fix** + the **long-standing CI format-drift fix**. All committed, pushed, **CI green** (first green since 2026-06-01).
- **~8,700 lines net removed** (legacy `_webui_configure` cluster + dead frontend/docs + planner dedup). Suite **1155 unit pass**, ruff + mypy clean.
- **The one thing NOT working:** the **Kendo dropdown WRITE path for non-default values** (e.g. DHCP subnet mask `255.255.255.128` /25; same blocker for ACL type/source-type, IP type, lease). 4 fix attempts, each peeled back a deeper layer; the current attempt (v3) is **uncommitted** and has a **precise, identified bug** (see §3/§5).
- Tags pushed: `backup-20260606-1428`, `backup-20260606-1549`.

---

## 1. What landed (committed on `feature/bootstrap`, CI green @ `a456d05`)

| commit | what |
|---|---|
| `7436f82` | **fix(security):** full-entropy `action_id` — `uuid4().hex[:6]` (24-bit) → full 128-bit. approve/execute endpoints are keyed only on this id and carry no auth (local-lab design), so a short suffix was brute-forceable. |
| `b720695` | **fix:** correctness bugs from `/review` — `retrieve.py` Chroma-None crash + `zip(strict=True)`; `cli_configure` pool-invalidate; `routes_suggestions` secondary-IP regex; `_subprocess` reader-thread error surfacing + idempotent `close()`; `plan_vision_check` missing `depth<0` JSON guard; `tool_registry` atlas-path `mark_failed` failure-context. |
| `3641443` | **chore:** gitignore `.mypy_cache/` (287 MB, was untracked+unignored). |
| `08bdbe9` | **refactor (Chunk B):** removed the legacy `_webui_configure` / `_propose_webui_configure` cluster + helpers (`_plan_hash`, `_act_ok`, `_plan_vision_counters`, `_WEBUI_CONFIGURE_MAX_ITER`, `draft_plan` import) + their tests. ~2,300 lines. Dispatcher has used the `_atlas` variants since Chunk C4; git history is the fallback. |
| `90c1d3a` | **style:** `ruff format .` — resolved pre-existing drift in 20 files. CI's `ruff format --check` had failed on **every** push since 2026-06-01; this is why CI is finally green. |
| `18d9a94` | **chore (Chunk C):** deleted `frontend-design-backup/` (34 files), `frontend/scraps/` (5); archived `docs/plan-dhcp-form-interaction.md` → `docs/history/`; refreshed stale "prototype/no-backend" wording. ~5,400 deletions. |
| `2ed7ea3` | **refactor (Chunk D):** consolidated the duplicated brace-balanced JSON parser into `backend/orchestration/json_extract.py` (repointed configure_planner / plan_vision_check / debug_planner / cli_configure_planner); added `write_tools._extract_device_errors()` helper. |
| `429bc27` | **fix(webui):** radio value-selector fix (see §3a). |
| `a456d05` | **docs:** `smoke-testing-guide.md` + `smoke-prompts-next.md`. |

Reviews also produced: `docs/smoke-testing-guide.md` (Mode A automated harness vs Mode B live), `docs/smoke-prompts-next.md` (ordered live prompts), and the dead-code findings (Chunk B/C confirm `docs/dead-code-findings.md`).

**Mode A automated smoke** (terminal, no app) ran clean: CLI read/show-run PASS against the live C1111-4P; write + RAG scenarios skip without `SMOKE_ALLOW_WRITES=1` / an ingested vectorstore.

---

## 2. Operational gotcha (carry forward)
The repo root `C:\GIT\AI_configurator` is on branch **`develop`** and its `backend/` has **only stale `.pyc`** — no source. All real code lives on **`feature/bootstrap`**, checked out in the worktree `loving-villani-1fe4d5`. Don't `git checkout feature/bootstrap` in the root (it's pinned to the worktree). Decide later whether to repoint/clean `develop`.

Also: CI's Node 20 deprecation annotation becomes a **hard failure on 2026-06-16** — bump `actions/checkout@v4→v5` and `actions/setup-python@v5→v6` in `.github/workflows/ci.yml` + `nightly-backup.yml`.

---

## 3. What does NOT work — the Kendo dropdown WRITE path

### 3a. Radio value-selector — ✅ FIXED (committed `429bc27`)
Static-route "IP Type" radio: a radio used as a value-selector was verified as a **boolean** (`"IPv4"` run through the truthy-token test → expected unchecked → a correctly-checked radio false-failed `verify_mismatch`). Fixed in BOTH the read-back verify and `_value_already_set` idempotent-skip: a radio's "set" state = the targeted radio is checked. **Live-smoke GREEN** (static route `10.50.0.0/24` configured end-to-end). +3 regression tests.

### 3b. Kendo dropdown non-default value — ❌ STILL FAILING (uncommitted v3)
**Symptom:** `step_failed: Field 'subnet-mask' failed: element_intercepted` when setting DHCP subnet mask `255.255.255.128` (/25). Textboxes (pool name, network) fill fine. Same blocker hits **ACL** type/source-type, and would hit IP type / lease. (Default /24 "worked" earlier only because it hit the idempotent-skip.)

**The KendoComboboxAdapter has 3 strategies** (`backend/webui_agent/atlas/adapters.py`):
1. **Widget JS API** — `kendo.widgetInstance(wrapper).value()`. Matches by VALUE only.
2. **Hidden `<select>`** — set the backing native select + dispatch change/input + drive widget + Angular + verify.
3. **Popup DOM click** — open dropdown, click `li.k-item` (interception-prone).

**The debugging peeled back four layers, each confirmed from `logs/actions.log` `kendo_select_*` events:**

| Attempt | Root cause found (from the log) | Fix tried | Why it still failed |
|---|---|---|---|
| 1 | `element_intercepted` — popup `li.k-item has_text` click times out (Strategy 2 was the popup) | Reordered so hidden-select runs before the popup click | Hidden-select still used a **blind 6-level DOM walk** that never reached the select |
| 2 | `backing select not found (walked 6 levels)` — the walk starts from the visible widget span; the `<select>` is `display:none` and lives outside that subtree | Use the **captured `kendo_select_name`** (like `read_back`): `page.locator("select[name='…']")` | **strict-mode violation**: two `<select>`s share `name="subnetmaskOptions"` (`id`s `subnetmaskOptions` + `subnetmaskOptionsDHCP`, identical `ng-model="dhcpScope.subnetmaskOptions"`) → `Locator.evaluate` throws → fell through to popup |
| 3 (v3, **uncommitted**) | strict-mode on duplicate same-name selects (Cisco Basic/Advanced/template render) | Pass the **visible widget `loc` as anchor**; `querySelectorAll`-pick the select that widget owns (no strict-mode); in-JS verify | **picker returns null → `no active backing <select> for name subnetmaskOptions`** → popup → `element_intercepted`. See the bug below. |

**Widget API (Strategy 1) fails every time** with `widget_instance_failed` — `kendo.widgetInstance(wrapper)` can't resolve the widget from the located visible span.

**The v3 picker bug (PRECISE, from `act_20260606_8fd0…`):** `pickActiveSelect()` found both candidates but rejected both:
- **Branch 1 (Kendo wrapper match)** relies on `window.jQuery` — apparently unavailable/ineffective on this page, so it never matched.
- **Branch 2 (container visibility)** walks up starting at the `<select>` itself — but the backing select is **always `display:none`** (Kendo hides it), so the very first iteration returns `false` for *every* candidate. The check should start at `select.parentElement` (skip the always-hidden select) and test the **ancestor form/section** visibility.

→ So the picker correctly avoids the strict-mode crash and fails loud (never writes a wrong copy), but it **can't yet identify the active select**. This is the open bug.

---

## 4. Uncommitted state (held for the live gate, per ship→smoke→commit)
```
 M backend/webui_agent/atlas/adapters.py      # the Kendo v1→v3 write-path iterations (v3 picker, has the §3b bug)
 M tests/unit/test_atlas_adapters.py          # updated Kendo adapter tests (55 pass) + 2 regressions
 M docs/smoke-findings-20260605.md            # appended Group C: static-route GREEN (C1), ACL deferred (C2)
```
These are **intentionally uncommitted** — the Kendo fix isn't live-green yet. Unit-green (1155) but the real gate (live smoke) is red. Keep them as the base for the next fix (do NOT revert — v3 is strictly better than the original: no strict-mode crash, fails loud).

---

## 5. What needs to be done (all)

### Immediate — finish the Kendo dropdown write path (the open blocker)
1. **Fix `pickActiveSelect` container-visibility (high confidence, ~5 lines):** start the visibility walk at `select.parentElement`, not the `<select>` (which is always `display:none`). Check the nearest ancestor form/section for `display:none` / `visibility:hidden` / `.ng-hide` / `aria-hidden`. The active copy's container is rendered; the template copy's isn't.
2. **Don't depend on `window.jQuery`** in branch 1 — confirm whether jQuery is exposed (`window.$`? noConflict?); if unreliable, drop branch 1 and rely on container-visibility, OR resolve the widget via the AngularJS scope.
3. **Re-smoke** (restart uvicorn → DHCP /25 prompt). Expect `kendo_select_success strategy="hidden_select"` with `candidate_count=2`, `select_id="subnetmaskOptionsDHCP"`; then **verify the real mask** via `show run | section dhcp` (the silent-wrong-value backstop — combobox read-back is advisory).
4. If container-visibility still can't disambiguate (both containers visible), fall back to one of the other Opus proposals (all in this session's workflow output): **capture the unique `id`** at perceive time (`subnetmaskOptionsDHCP`) and resolve `select[id=…]`; or set the **AngularJS model directly** from the visible widget's scope (`dhcpScope.subnetmaskOptions`).
5. Once green: re-smoke **ACL** (acl-type + source-type dropdowns), **IP type**, **lease** — all flow through this same path.
6. Commit the Kendo fix + tests, roll a `backup-` tag, update `smoke-findings` C2 to fixed.

### Then — roadmap (from `next-session-kickoff.md` + `smoke-findings-20260605.md`)
- **Prove breadth:** smoke VLAN, interface description (mostly textbox — likely easy), more sections through the one engine.
- **FEAT-SMART** (Phase G): Advanced-tab capture + capability resolver + clarify/suggest (OSPF area, DHCP gateway/DNS live on Advanced).
- **CLI bugs F1–F5:** F2 (`set_interface_ip` prepends `no switchport`, breaks routed Gi0/0/x) is HIGH; F1/F3/F4/F5 medium/low.

### Housekeeping
- **CI Node 20→24** action bump (hard-fails 2026-06-16).
- Delete merged `cleanup/*` branches; decide on stale `develop`.
- Deferred quality refactors: full `write_tools` write-flow extraction, `capture.py` dedup, `schema._dict_or_none`, `read_tools._run` split, `vision_fallback`'s separate JSON-parser copy, remove `draft_plan` (+ its tests).

---

## 6. Refs
- Skill written this session: `.claude/skills/webui-kendo-dropdown/SKILL.md` (the dropdown write-path knowledge + diagnostic discipline).
- Diagnostic gold: `logs/actions.log` → grep the `action_id` + `kendo_select_*` events. Each smoke = one action_id; read the strategy events before changing anything (`live-smoke-iteration` rule #2).
- Commits/tags/branches: `feature/bootstrap` @ `a456d05` (pushed, CI green); tags `backup-20260606-1428`, `backup-20260606-1549`; branches `cleanup/remove-legacy-webui-configure`, `cleanup/chunk-cd` (both merged into feature/bootstrap).
