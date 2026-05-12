# Playwright codegen against the real C1111

This walks the two demo flows in headed Chromium while Playwright records
every click + locator as Python source. The captured output is the ground
truth for the selectors yaml — anything in `backend/webui_agent/selectors/iosxe_default.yaml`
should match what codegen sees.

## When to do this

- **Day 4** (one-time bootstrap) — capture both flows once, lift the
  selectors into the yaml, commit.
- **Whenever the C1111 firmware upgrades** — selectors may change between
  IOS XE 17.x point releases. If a Day-5/7 flow starts failing with
  `evidence_dom_dump` files, rerun codegen on the failing step.

## Prereqs

`.env` populated (`ROUTER_WEBUI_BASE_URL`, `ROUTER_WEBUI_USER`,
`ROUTER_WEBUI_PASSWORD`) and the router cabled + reachable. SSH host key
already accepted on this machine.

## Run it

From the worktree root with the venv active:

```powershell
python -m playwright codegen `
  --target python `
  --output playwright_playground/draft_real_router_codegen.py `
  --ignore-https-errors `
  --viewport-size=1400,900 `
  https://192.168.10.1
```

Two windows open: **Chromium** (you click in this) and the **Playwright
Inspector** (this writes the code as you click).

Note: `draft_real_router_codegen.py` is gitignored — it contains the
live router URL and is a one-time exploration artifact, not production
code.

## What to walk

### Flow 1 — Hostname change (Administration → Device Properties)

1. Log in (`cisco` / your password)
2. Top nav → **Administration**
3. Submenu → **Device Properties**
4. Click into the **Hostname** field (don't change the value)
5. Click **Apply** *but cancel before submitting* — Esc or click somewhere
   outside the dialog. We don't want to actually change the hostname; we
   just need codegen to capture the form-element selectors.

### Flow 2 — VLAN add (Configuration → Layer 2 → VLAN → Add)

1. (Still logged in)
2. Top nav → **Configuration**
3. Submenu → **Layer 2** → **VLAN**
4. Click the **Add** button on the VLAN list
5. Fill VLAN ID = `99`, VLAN Name = `TEST-CODEGEN`
6. Click **Cancel** (don't save — the same gotcha as flow 1).

Total walk time: 3–5 minutes.

## What to lift into the yaml

Open `playwright_playground/draft_real_router_codegen.py`. For each step
captured, you'll see something like:

```python
page.get_by_label("VLAN ID").click()
page.get_by_label("VLAN ID").fill("99")
```

Update `backend/webui_agent/selectors/iosxe_default.yaml`:

```yaml
vlan_form:
  vlan_id:
    - { label: "VLAN ID" }       # ← from codegen, most stable
    - { css: "input[name='vlanId']" }  # ← fallback if label fails
```

The fallbacks already in the yaml are seeded from scripts/05 + scripts/06.
Codegen tells you which one is the primary on **your specific firmware**.

## Verify after editing

```powershell
python -m pytest tests/unit/test_webui_selectors.py -q
```

All tests should still pass (they assert structure, not specific
selectors). If a test fails, you broke the yaml shape — check indentation
and that every strategy is a dict with at least one of `role`/`label`/
`text`/`css`.

Then run script 06 in dry-run mode to verify the new selectors work
end-to-end:

```powershell
python playwright_playground/scripts/06_real_router_vlan_add.py
```

(Default `PLAYWRIGHT_DRY_RUN=true` clicks Cancel before save.)

## When in doubt

The DOM dump beats guessing. If a selector flakes:

1. The flow code that fails calls `EvidenceCollector.dump_dom(page)` —
   look in `artifacts/screenshots/<flow>_<session>/dom.html`.
2. Open the HTML, find the element you wanted, copy its actual `name=`,
   `id=`, or visible text.
3. Add a strategy to the yaml that matches it.
4. Re-run.
