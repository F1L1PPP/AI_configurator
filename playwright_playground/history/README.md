# playwright_playground/history — archived learning scripts

These scripts were written during Day 2–5 as we learned Playwright against the
fake-WebUI mock and the real C1111. They're **not** imported by `backend/` or
the tests — production WebUI flows live in `backend/webui_agent/flows/` and
use page objects in `backend/webui_agent/pages/`.

Kept here as tribal knowledge for the next developer who needs to:

| Pattern                                | See                                  |
|----------------------------------------|--------------------------------------|
| Selector strategy fallback chain       | `01_basic_nav.py`                    |
| Form submit + validation               | `02_form_submit.py`                  |
| Post-action verification via CLI       | `03_verify.py`                       |
| Error handling (modals, timeouts)      | `04_error_handling.py`               |
| Probing an unknown WebUI route safely  | `05_real_router_probe.py`            |
| End-to-end VLAN add against real device| `06_real_router_vlan_add.py`         |
| DOM dump + screenshot helpers          | `_helpers.py`                        |

If you want to learn the WebUI manually before writing a new flow, read these
in order. If you want to ship a new flow, copy the structure of
`backend/webui_agent/flows/change_hostname.py` instead — it's the canonical
shape (login → page object → screenshot each step → CLI verify → mark_executed).

The directory is excluded from `pytest` discovery (it has no `test_*.py` files)
and is not imported anywhere — safe to delete entirely once the team is
comfortable.
