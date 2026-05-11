# How Playwright Clicks — Training Manual

> The "manual" for the WebUI agent. Read this once before Day 3 probe and Day 4
> discovery. The 4 scripts in `scripts/` are this manual made executable —
> they implement every pattern below against the local fake site.

This document is the source of truth for our WebUI automation patterns. The
production code under `backend/webui_agent/` (Days 4–5, 8) follows the same
rules.

---

## 0. The mental model

**Playwright drives a real browser.** Not a fake DOM, not requests — an actual
Chromium process that loads the page, runs the JavaScript, and clicks pixels.
Same as a human, just scripted.

That has two consequences:

1. **Everything is async-ish.** Pages render after JS runs. Forms submit and
   redirect. Modals fade in. You can't just "click and read" — you must
   `wait_for` the state you expect.
2. **Selectors are the contract.** If the WebUI re-renders and a button's CSS
   class changes from `.btn-primary` to `.button--primary`, your script breaks.
   Picking *stable* selectors is 80% of the work.

---

## 1. Locator strategy — pick in this order

When you have a choice, prefer the higher-up strategy:

| # | Strategy | Why | Example |
|---|---|---|---|
| 1 | **`get_by_role`** | Maps to accessibility tree — survives CSS rewrites, framework rewrites, design refreshes | `page.get_by_role("button", name="Save")` |
| 2 | **`get_by_label`** | Form fields have stable label text even when class names change | `page.get_by_label("VLAN ID")` |
| 3 | **`get_by_text`** | Visible text is stable for action-oriented UIs ("Add VLAN", "Log out") | `page.get_by_text("+ Add VLAN")` |
| 4 | **`get_by_test_id`** | If we control the markup and added `data-testid` attrs (we won't on Cisco WebUI) | `page.get_by_test_id("vlan-save")` |
| 5 | **CSS / XPath** | Last resort. Brittle. Use only when 1–4 don't disambiguate | `page.locator("input[name='vlan_id']")` |

**Why this order matters:** Cisco's WebUI uses minified CSS class names that
change between IOS XE versions. Role + label + text survive those changes.

---

## 2. Auto-waiting — Playwright already waits

You almost never need explicit sleeps. The locators below auto-wait for the
element to exist, be visible, be enabled, and be stable before the action:

- `.click()`
- `.fill(text)`
- `.select_option(label=…)`
- `.check()` / `.uncheck()`
- `expect(locator).to_be_visible()` / `.to_contain_text(…)`

Default timeout is **30 seconds**, configurable per call: `.click(timeout=5000)`.

**When you DO need explicit waits:**

- `page.wait_for_url("**/dashboard.html")` — after a `window.location` redirect
  (these are not navigations Playwright auto-detects)
- `page.wait_for_load_state("networkidle")` — after navigating to a page that
  loads data via JS (the Cisco WebUI does this a lot; the playground does it
  via `fakeDelay`)
- `page.wait_for_selector("table tbody tr")` — when you need a child element to
  be rendered, not just the parent

---

## 3. The mandatory rituals on EVERY write

This is non-negotiable in the Cisco WebUI work, and the playground scripts
demonstrate all of them:

1. **Screenshot before AND after every step.** Even if nothing visible
   changed. Numbered, in `artifacts/screenshots/<session>/`.
2. **On exception: screenshot + DOM dump + URL + console logs, then ABORT.**
   See `scripts/04_error_handling.py`. NEVER auto-retry a write. The fix
   might be: the form half-submitted, the router is in a weird state, retrying
   would compound the problem.
3. **Verify the change after the write.** A WebUI write isn't trusted until
   we observe the new state — either through the same WebUI (read back the
   list view) OR through a different channel (CLI `show running-config`).
   See `scripts/03_verify.py`.

---

## 4. Headed vs headless mode

| Mode | When | How |
|---|---|---|
| **Headed** (browser window visible) | Development, debugging, demos, codegen | `chromium.launch(headless=False)` |
| **Headless** (no UI) | CI, smoke loop, production | `chromium.launch(headless=True)` (default) |

`slow_mo=400` adds a 400ms pause between actions — invaluable when watching a
flow play out in headed mode. Always remove for CI.

---

## 5. Self-signed certs (the Cisco WebUI gotcha)

The C1111 WebUI uses a self-signed certificate. Browsers normally refuse to
load it. Playwright must be told to bypass:

```python
context = browser.new_context(ignore_https_errors=True)
```

Apply this at the **context** level, not on individual `page.goto()` calls —
the cert error fires before `goto()` returns and would crash earlier.

The playground site is HTTP so this isn't exercised, but every real-router
script will need it.

---

## 6. `playwright codegen` — recording selectors

The single most useful tool for figuring out selectors on a site you didn't
build (like the Cisco WebUI):

```bash
python -m playwright codegen http://localhost:8765
```

It opens a browser AND an inspector window. As you click around the browser,
the inspector writes Python code in real time. Copy what you need, swap to
stable role-based selectors where codegen picked CSS.

**Day 4 plan:** run codegen against the real C1111 WebUI to capture the
selectors for Login → Add VLAN → Save → Verify, then rewrite the recorded CSS
selectors as role/label/text equivalents in `webui_agent/selectors/iosxe_default.yaml`.

---

## 7. Traces — the "replay the failure" tool

Enable trace recording in production / CI:

```python
context = browser.new_context(...)
context.tracing.start(screenshots=True, snapshots=True, sources=True)
try:
    # ... do work ...
    context.tracing.stop(path="artifacts/traces/<session>.zip")
except Exception:
    context.tracing.stop(path="artifacts/traces/<session>-FAILED.zip")
    raise
```

To replay a trace:

```bash
python -m playwright show-trace artifacts/traces/<session>.zip
```

You get a frame-by-frame UI: every action, the DOM at each step, network
requests, console messages. This is how you debug "it failed once on the
demo machine but works locally."

For the playground scripts I left tracing off — it adds noise to the training
flow. For Day 5+ production scripts we always trace on failure.

---

## 8. The four practice scripts — what each proves

| Script | Demonstrates | Maps to plan day |
|---|---|---|
| `01_basic_nav.py` | Headed launch, role-based locators, `wait_for_load_state`, screenshots between steps | Day 3 WebUI probe |
| `02_form_submit.py` | Filling multiple input types, `select_option(label=…)`, `wait_for_url` after redirect | Day 5 hostname / Day 8 VLAN flows |
| `03_verify.py` | `expect(locator).to_*` assertions, table-row matching with `has_text=`, non-zero exit on failure | Day 5+ verification pattern |
| `04_error_handling.py` | Wrapping clicks in try/except, `PWTimeout`, screenshot + DOM dump + URL + console capture, NO RETRY | Every write tool — CLAUDE.md hard rule #5 |

Run them in order. By the end you've exercised every pattern the Day 4–8
WebUI work needs.

---

## 9. Common failure modes (and what to do)

| Symptom | Cause | Fix |
|---|---|---|
| `TimeoutError: locator.click: Timeout 30000ms exceeded` | Selector doesn't match anything, or element hidden | Open the page in `playwright codegen`, copy the actual selector |
| Form submits but verification fails | Page rendered before async data loaded | Add `page.wait_for_selector("table tbody tr")` before reading |
| Works headed, fails headless in CI | Timing differs without slow_mo; selectors that depend on focus state | Add explicit `wait_for_load_state("networkidle")` |
| Self-signed cert warning page | `ignore_https_errors=True` not set on the context | Set it at context creation, not on `page.goto` |
| Session timeout mid-flow (Cisco WebUI ~5 min idle) | Sat too long between actions | Detect the login redirect, relogin, retry the read (NOT the write) |

---

## 10. The hard rule one more time

**A write that fails halfway is more dangerous than a write that fails
immediately.** Retrying a half-completed write can compound state corruption.

The discipline:

1. One write attempt per approved `action_id`.
2. On failure: save evidence, surface to a human, stop.
3. The human decides: roll back from snapshot, or accept current state, or try
   again as a new approved `action_id`.

The orchestrator can suggest CLI fallback for retries (`webui_change_hostname`
fails → suggest `cli_set_hostname`), but it's a NEW action with NEW approval,
not a silent retry of the WebUI one.
