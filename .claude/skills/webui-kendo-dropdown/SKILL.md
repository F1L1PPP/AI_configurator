---
name: webui-kendo-dropdown
description: >-
  Debugging/fixing Kendo dropdown + Kendo-backed widget selection in the atlas WebUI driver on the
  Cisco C1111-4P. Invoke when a live smoke fails on a WebUI dropdown/select/radio — symptoms like
  `element_intercepted`, `verify_mismatch`, `widget_instance_failed`, `backing select not found`, or a
  strict-mode violation on `select[name=...]` — or when touching KendoComboboxAdapter / RadioAdapter /
  the `_JS_*` constants / the act-path read-back verify. Encodes the 2026-06-06 Kendo write-path saga.
---

# WebUI Kendo dropdown write path

Setting a Cisco Kendo dropdown to a **non-default** value (e.g. DHCP subnet mask `255.255.255.128`)
is the hardest part of the atlas WebUI driver. Reading it back is easy; *writing* it reliably — so the
form actually submits the chosen value — is not. This skill is the map.

Code: `backend/webui_agent/atlas/adapters.py` (`KendoComboboxAdapter`, `RadioAdapter`, the `_JS_*`
constants, `_first_visible`). Act loop + verify: `backend/webui_agent/_playwright_subprocess.py`
(`_do_act_by_field`, `_value_already_set`). Capture: `backend/webui_agent/atlas/capture.py`
(`kendo_select_name`). Diagnostic gold: `logs/actions.log` → grep the `action_id` + `kendo_select_*`.

## The widget-adapter contract (load-bearing)
Each adapter does **apply** (set the value), **read_back** (read current value), and the act loop adds
a **read-back verify**. Failure taxonomy in `_do_act_by_field`:
- `PlaywrightTimeoutError` → `element_intercepted` (retried once). NEVER swallow it.
- `ValueError` → dead-end (value not in options) — not retried.
- combobox read-back verify is **advisory** (the backing select's value attr legitimately differs from
  display text), so the **real backstop is the post-Apply CLI running-config check**. A write must
  therefore *fail loud* (fall through / raise) if it didn't take — never silently report success.

## Cisco Kendo DOM facts (the whole problem)
1. The backing `<select>` is **`display:none`** (Kendo renders a visible `<span>` widget over it).
   Visibility checks on the select itself are useless — check its **ancestor container**.
2. Cisco renders **duplicate `<select>`s with the SAME `name`** across Basic/Advanced/template
   sections (e.g. two `name="subnetmaskOptions"`, ids `subnetmaskOptions` + `subnetmaskOptionsDHCP`,
   identical `ng-model`). A global `page.locator("select[name=...]")` matches >1 → **Playwright
   strict-mode violation**. (Same root pattern as OSPF `processID`×4 that `_first_visible` handles for
   visible inputs.)
3. Option **value ≠ display text**: mask shows "255.255.255.128" but the option `value` is "25". So
   match by value **OR** text (case-insensitive, trimmed).
4. `kendo.widgetInstance(span)` often **fails** (`widget_instance_failed`) — the widget instance is on
   the `<select>` element, not the wrapper span the locator returns.
5. The popup-click path (open dropdown → click `li.k-item`) is **interception-prone** (scrolls/overlay
   → `element_intercepted`). Avoid relying on it.

## KendoComboboxAdapter strategies (current order)
1. **Widget JS API** (`_JS_WIDGET_API`) — `kendo.widgetInstance().value()`. Matches by value only;
   currently fails `widget_instance_failed` on the DHCP form.
2. **Backing `<select>` from the widget** (`_JS_SELECT_FROM_WIDGET` + `_JS_PICK_FN`) — pass the visible
   widget as anchor, `querySelectorAll`-pick the select that widget owns, set + drive Kendo + Angular +
   verify. **No popup.** This is the strategy to make bulletproof.
3. **Popup DOM click** (last resort) — interception-prone.

## Failure mode → log signature → cause
| `kendo_select_*` event / reason | Cause |
|---|---|
| `strategy1_unavailable reason=widget_instance_failed` | `kendo.widgetInstance(span)` can't resolve — widget is on the `<select>`, not the span |
| `strategy_hidden_unavailable reason="backing select not found (walked 6 levels)"` | a DOM walk from the visible span can't reach the `display:none` select (it's outside that subtree) — **don't walk; use a name/id or the widget anchor** |
| `strategy_hidden_error … strict mode violation … resolved to 2 elements` | duplicate same-name `<select>` — `select[name=...]` is ambiguous → **disambiguate** |
| `strategy_hidden_unavailable reason="no active backing <select> for name X"` | the querySelectorAll-picker found candidates but rejected all (see open bug) |
| `webui_act_field_soft_failure failure_reason=element_intercepted` | fell through to the popup click and it timed out |
| `failure_reason=verify_mismatch` | apply ran but read-back disagreed (for radios: the value-selector-as-boolean bug, FIXED) |

## What's been tried (2026-06-06) and the OPEN bug
Four attempts, each confirmed from the log (`docs/today-2026-06-06-summary.md` §3 has the table):
1. reorder hidden-select before popup → still used a blind 6-level walk → `backing select not found`.
2. locate by captured `kendo_select_name` (`select[name=…]`) → **strict-mode** on duplicate names.
3. (v3, **uncommitted in `adapters.py`**) anchor on the visible widget + `querySelectorAll`-pick →
   **picker returns null** (`no active backing <select>`).

**OPEN BUG in `pickActiveSelect` (the precise next fix):**
- Branch 2 (container visibility) walks up starting at the `<select>` itself, which is **always
  `display:none`**, so it returns false for *every* candidate. → start the walk at
  `select.parentElement` and check the **ancestor form/section** visibility.
- Branch 1 (Kendo wrapper match) relies on `window.jQuery`, which is unavailable/ineffective on this
  page. → don't depend on it; rely on container-visibility, or resolve via the AngularJS scope.

## Fallback approaches (if container-visibility can't disambiguate)
From the 8-agent Opus analysis (all in this session's workflow output):
- **Capture the unique `id`** at perceive (`subnetmaskOptionsDHCP` is unique even when name isn't);
  resolve `select[id=…]`. Capture only emits VISIBLE widgets, so the id it records is the active one.
- **Set the AngularJS model directly** from the visible widget's scope
  (`scope.dhcpScope.subnetmaskOptions = optValue; $apply()`) — both duplicate selects share the model,
  so the form submits correctly regardless of DOM.

## Diagnostic discipline (do this every smoke)
1. Each smoke = one `action_id`. `grep <action_id> logs/actions.log` and read the `kendo_select_*`
   strategy events **before changing anything** — name the failure mode + the exact reason.
2. Restart `uvicorn` before re-smoking — the driver runs in a child subprocess that must reload the
   adapter code.
3. The in-JS verify (re-read the set value + widget agreement) is the **fail-loud gate** — keep it; it
   prevents a wrong-copy write from reporting success.
4. **Verify the real config** after a "success": `show run | section dhcp` (or the relevant section).
   Combobox read-back is advisory — never trust "no exception" as proof the value submitted.
5. Hold the commit until the **live smoke is green AND the real config is correct** (ship→smoke→commit).

## Related: radio value-selectors (same widget-adapter class, FIXED `429bc27`)
A radio used as a value-selector (`"IP Type" = IPv4`) must not be verified as a boolean. Its "set"
state = the targeted radio is checked — in BOTH the read-back verify and `_value_already_set`. Don't
run an option label through the truthy-token test. (Generalize this instinct: every widget's
apply/read-back/verify must agree on what "set" means for THAT widget.)

## Anti-patterns
- ❌ Global `select[name=...]` — strict-mode-fails on Cisco's duplicate names.
- ❌ `.first` on the named selects — DOM order puts the **template/hidden copy first** → silent-wrong-value.
- ❌ Visibility check on the `<select>` — it's always `display:none`; check the ancestor container.
- ❌ DOM-walk from the visible span to find the select — it's outside that subtree.
- ❌ Trusting the popup DOM click — interception-prone (`element_intercepted`).
- ❌ Reporting success without verifying the real submitted config.

## Reference
- `docs/today-2026-06-06-summary.md` — the full saga + commits/tags.
- `docs/smoke-findings-20260605.md` — Group C (static route GREEN, ACL deferred).
- `live-smoke-iteration` skill — the ship→smoke→triage discipline this builds on.
