# Playwright Playground

A self-contained training ground for the WebUI agent work in Days 3–5 and 8.

The agent doesn't touch the real C1111 here — instead it drives a fake
"Cisco-Lab WebUI" we serve locally. That way you can practice the patterns,
surface install issues, and watch flows in headed mode without any risk to a
real device.

When the real router work starts on Day 3, the patterns and the directory
shape carry over 1:1 — the only diff is the `BASE_URL`.

## Read this first

[`docs/playwright_manual.md`](docs/playwright_manual.md) — the "how Playwright
clicks" manual. Locator strategy, auto-wait rules, error handling, the four
practice scripts, common failure modes.

## What's in here

```
playwright_playground/
├── README.md                       ← this file
├── docs/
│   └── playwright_manual.md        ← read this
├── site/                           ← the fake Cisco-Lab WebUI (HTML/CSS/JS)
│   ├── index.html                  ← login page (admin / admin)
│   ├── dashboard.html              ← post-login
│   ├── vlan-list.html              ← VLAN table (state from localStorage)
│   ├── vlan-add.html               ← form to add a VLAN
│   ├── style.css                   ← Cisco-WebUI-ish minimal styling
│   └── app.js                      ← auth + VLAN state (localStorage)
├── serve.py                        ← serves site/ on http://localhost:8765
├── scripts/
│   ├── _helpers.py                 ← shared: session dirs + screenshot helper
│   ├── 01_basic_nav.py             ← login → dashboard → VLAN list, screenshots each step
│   ├── 02_form_submit.py           ← navigate → fill VLAN form → save → redirect
│   ├── 03_verify.py                ← submit + assert the new row exists; non-zero exit on fail
│   ├── 04_error_handling.py        ← intentional bad selector → screenshot + DOM dump + abort
│   ├── 05_real_router_probe.py     ← REAL C1111: cert/login/nav probe, proves priv-15 menus
│   └── 06_real_router_vlan_add.py  ← REAL C1111: navigate to VLAN form + fill (dry-run safe)
└── artifacts/                      ← screenshots + DOM dumps, gitignored
    └── <script>_<timestamp>/
```

## Run it (one-time setup)

Already done by the install steps in the root `README.md`, but for reference:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # pins playwright==1.49.1
python -m playwright install chromium    # downloads ~120 MB Chromium
```

## Run it (every time)

Open two terminals.

**Terminal 1 — serve the fake site:**

```powershell
python playwright_playground\serve.py
# → Playwright playground site at http://localhost:8765/
# → leave running, Ctrl+C to stop
```

**Terminal 2 — run a script:**

```powershell
python playwright_playground\scripts\01_basic_nav.py
```

A Chromium window will open and you'll watch the script click through the
flow. Screenshots land under `playwright_playground/artifacts/<script>_<timestamp>/`.

## The four fake-site scripts — in order

| Script | What you'll see | What you'll learn |
|---|---|---|
| `01_basic_nav.py` | Browser opens, types admin/admin, clicks Log in, lands on Dashboard, clicks VLANs link, lands on the list | Headed mode, role-based locators, screenshot per step |
| `02_form_submit.py` | Same as 01 then clicks `+ Add VLAN`, fills four form fields, clicks Save, redirects to list with the new VLAN row | Filling multiple input types, dropdowns, `wait_for_url` after redirect |
| `03_verify.py` | Same as 02 then asserts the success banner mentions VLAN 42 AND a table row with "ENGINEERING" exists. Prints PASS or FAIL | `expect(locator).to_*` assertions, the verification pattern, non-zero exit on failure |
| `04_error_handling.py` | Logs in, then deliberately tries to click a link that doesn't exist. Catches the timeout, saves screenshot + DOM dump, prints the URL + console messages, exits 2. **Does NOT retry** | Error capture, the "no auto-retry on write" hard rule |

## Real-router scripts

These require the C1111 to be cabled and `.env` populated.

| Script | What it does | Safe to run? |
|---|---|---|
| `05_real_router_probe.py` | Login + nav check — confirms priv-15 menus are visible | Yes — read-only |
| `06_real_router_vlan_add.py` | Login → Configuration → Layer 2 → VLAN → Add → fill → **cancel** | Yes by default (`PLAYWRIGHT_DRY_RUN=true`) |

**Run script 06:**

```powershell
# dry-run (default) — fills the form but clicks Cancel, nothing saved
python playwright_playground\scripts\06_real_router_vlan_add.py

# live — actually creates VLAN 99 on the router
$env:PLAYWRIGHT_DRY_RUN = "false"
python playwright_playground\scripts\06_real_router_vlan_add.py
```

If the script can't find a nav element it dumps a `*-dom.html` file in the
session folder — open that file in a browser or editor to find the real
selector names, then adjust the fallback lists at the top of script 06.

## How this maps to the real Cisco WebUI work

| Playground | Production (Days 3–8) |
|---|---|
| `BASE_URL = "http://localhost:8765"` | `BASE_URL = settings.router_webui_base_url` (HTTPS, self-signed) |
| `Step` helper in `_helpers.py` | `webui_agent/evidence.py` (Day 4) |
| Login form, `admin`/`admin` | Login page on real router, real credentials from `.env` |
| VLAN list / add (localStorage-backed) | Configuration → VLANs in real Cisco IOS XE WebUI |
| `expect(row).to_be_visible()` | Same call, against the real DOM |
| Manual run, watch headed | Same when developing, `headless=True` in CI smoke loop (Day 9) |

## Why this exists (the project rationale)

The Day 3 plan (`PROJECT_PLAN.md §7`) includes a 30-min "WebUI cert/login
probe" against the real C1111. Without this playground, that probe is the
first time we'd touch Playwright at all — multiple unknowns at once:

- Is Playwright + Chromium installed correctly?
- Do my selectors look right?
- Does `ignore_https_errors=True` actually work against this cert?
- Do `wait_for_load_state` semantics match my expectations?

This playground isolates and answers the first three locally. The Day 3 probe
then only has to answer the last one (Cisco-specific) plus the cert question.

## Cleanup

Delete the artifacts folder anytime — it's gitignored:

```powershell
Remove-Item -Recurse -Force playwright_playground\artifacts
```

The whole playground folder is excluded from the final submission ZIP
(Day 10 — `scripts/create_release_bundle.py`).
