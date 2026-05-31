# Plan — DHCP WebUI Form Interaction (post-Kendo)

> **Status:** drafted 2026-05-31. The Kendo-dropdown blocker is **FIXED** (commits
> `24071b2`, `ea8eda5`, `95a66f1` + cap revert) and unit-green (783 tests, ruff clean).
> The live DHCP smoke still fails because the form-filling loop **cycles** instead of
> converging. This doc scopes the real fixes. **Push stays gated on a green smoke** (Filip's rule).

## What already works
- Kendo dropdowns (Subnet Mask, IP Type) select cleanly via the hidden-`<select>` path
  (`kendo_select_success` in the live log). Case-insensitive match handles intent `"IPv4"`
  vs option text `"IPV4"` / value `"ipv4"`.
- The planner produces a **correct** plan (Pool Name → Network → Starting ip → Ending ip → Apply).

## Why the smoke still fails — evidence: run `act_20260531_43fbca` / `sess_03dd9c4c`
The execute loop ran **10 iterations / 26 steps / ~18 min** and never reached a verified Apply.
It **cycles**: fill fields → click **"Cancel"** → form resets → re-fill → repeat. Four compounding causes:

### 1. "Apply to Device" is never surfaced — THE loop driver
- DOM (`…/99-act-error-e_013.html:17167`): the control is `<span class="fa pl-save"></span> Apply to Device`
  inside a non-`<button>` wrapper.
- `describe_page` (`semantic_dom._UNION_SELECTOR` / `_classify_role`) never surfaces it as clickable, so
  it's absent from the view's elements. The described buttons stop at `e_027 "Add"` / `e_030 "Cancel"`.
- → the executor can't resolve "click Apply to Device" → heuristic/vision mis-resolves to the nearest
  button **`e_030 "Cancel"`** → **clicks Cancel → form resets** → re-fills → loop.
- **Fix:** make `describe_page` surface the Apply control (broaden `_UNION_SELECTOR` to catch
  `span.pl-save` / its clickable parent, or add a role classification). File: `backend/webui_agent/semantic_dom.py`.

### 2. Destructive "Cancel" gets clicked
- Downstream of #1, but dangerous on its own — the resolver should never satisfy an Apply/Save/Submit
  intent with a Cancel/Close control.
- **Fix:** in the resolve/deny path, refuse to resolve a submit-intent onto a cancel/close element
  (treat "Cancel" as a non-target unless the intent *is* cancel). File: `backend/webui_agent/_playwright_subprocess.py`
  (`_do_act_by_intent` / `_SENSITIVE_DENY_LIST` neighbourhood).

### 3. Network / Starting-IP / Ending-IP textboxes mislabeled
- DOM: `<input name="networkIp" ng-model="dhcpScope.networkIp" placeholder="xxx.xxx.xxx.xxx">`
  (likewise `startingIp`, `endingIp`). **No `id`, no `aria-label`, no adjacent `<label>`** → `_resolve_name`'s
  `_spatial_label` grabs nearby value-text (`"10"`, `"255.255.255.0"`).
- → planner's "Network"/"Starting ip" don't match the described names → vision fallback → churn + wrong resolutions.
- **Fix:** prefer the input's `name` / `ng-model` tail as a label source when spatial-label yields a
  value-like / low-quality result (map camelCase → words: `networkIp` → "Network IP"), or add a resolution-time
  match against `name`/`ng-model`. File: `semantic_dom.py:_resolve_name` (± `login.first_match`).
  **Scope carefully** — shared by all forms.

### 4. Pool-name validation quirk
- Two pool-name controls: a disabled `<textarea name="scopeName" ng-disabled="true">` shadowing the editable
  input (`e_025`). The form reported `"Please provide a valid DHCP pool name"` mid-loop.
- Likely a side effect of the Cancel-reset (#1), or the editable field needs a blur/change to register.
- **Fix:** revisit after #1–#3; if it persists, fire a blur/change after filling Pool Name. File: TBD.

## Sequence (fix the loop driver first)
1. **#1 Apply surfacing + #2 Cancel deny** — stops the cycle. Re-smoke: expect it to reach Apply.
2. **#3 textbox labeling** — removes the vision churn → fast + fewer iterations.
3. **#4 validation** — only if it persists after 1–2.

Each chunk: mock unit tests (describe/resolve) → **deep audit** (smoke-touching) → live re-smoke.

## Cap
`_WEBUI_CONFIGURE_MAX_ITER` reverted to **4** (the 10 bump didn't help — it cycled). Re-tune once the form
converges (a cleanly-progressing run needs ~5–6 iterations).

## Risk
- #1 and #3 touch `describe_page` / resolution — shared by **all** WebUI forms → regression risk → deep
  audit + run the existing VLAN/hostname WebUI tests before re-smoke.
- The "fast like Claude in Chrome" gap is real: a general computer-use agent sees the rendered button and
  reads labels visually; our DOM-first path needs these specific surfacing/labeling fixes to match on this
  idiosyncratic Cisco form.

## Committed so far (unpushed, green-smoke-gated)
`24071b2` chunk-1 Kendo · `ea8eda5` chunk-2 quick · `95a66f1` Kendo evaluate-sig fix · cap revert.
783 unit tests green, ruff clean. Pre-existing mypy drift in `configure_planner.py:369,392` tracked separately.
