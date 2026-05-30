# Dead-code findings — deferred cleanup

> **Status:** advisory only — **nothing has been removed.**
> **When to act:** **after the WebUI / vision configure driver is functional.** Some "dead" items below are deliberately-retained reference / safety-net backups we may still consult while finishing the driver — sweep them *then*, not now.
> **Generated:** 2026-05-30 by the `dead-code-audit` skill — 25 Opus 4.8 read-only agents (find → adversarial verify → synthesize). **60 confirmed-dead, 11 false-positives dropped** by the verify stage.

---

## A. Remove — safe, zero runtime impact

### Whole dead trees (the bulk — ~60 files)
- [ ] `frontend-design-backup/` — **entire tree (~40 files)**: an unbuilt/unserved Next.js app. Holds the repo's *only* `package.json` / `next.config.mjs` / `tailwind.config.ts`; all `app/` routes (`page`, `chat`, `webui-live`, `preview`, `actions`+3 subroutes); `lib/{api,ws,errors}.ts`; **all 21 `components/*.tsx`**. Live UI is `frontend/*.jsx` served by StaticFiles. Nothing in CI / pre-commit / Docker / `main.py` builds, serves, imports, or tests it.
- [ ] `playwright_playground/` — **entire directory (~22 files)**: `serve.py`, mock `site/`, `history/01–07_*.py`, `_helpers.py`, `history/__init__.py`, `docs/playwright_manual.md`. Excluded from pytest (`testpaths=["tests"]`); router scripts superseded by `backend/webui_agent/flows/`.

### Orphaned manual scripts (3)
- [ ] `tools/query_rag.py`  ·  [ ] `tools/check_vectorstore.py`  ·  [ ] `scripts/run_smoke_tests.py`
  (`__main__`-only; no importer/test; CI already runs `pytest` directly.)

### Frontend
- [ ] `frontend/scraps/*.napkin` (4 files) + the stray `.thumbnail.png` — git-tracked, zero references.
- [ ] `frontend/mock-data.jsx` exports: `CHAT_SCRIPTS`, `INITIAL_CHAT`, `MOCK_DEVICES`, `HEALTH_SUMMARY`, `matchScript`, `buildExecuteStream` (synthetic mock path; real data flows via `window.api` / `/ws/agent`).
- [ ] `frontend/chrome.jsx`: `IconBrowser`, `IconUndo` (defined+exported, never rendered).
- [ ] `frontend/tweaks-panel.jsx`: `TweakText`, `TweakNumber` (defined+exported, no caller).

### Backend
- [ ] `backend/db/` — empty (only `migrations/.gitkeep`); the never-built "Day 12" SQLite scaffold.
- [ ] `backend/core/settings.py`: `rag_top_k` field — defined, never read (callers hardcode `top_k`).
- [ ] `backend/orchestration/plan_vision_check.py`: `_SUCCESS_LOG_CACHE_TTL_SECS` — unused (cache is mtime-keyed, no TTL).
- [ ] `backend/webui_agent/pages/hostname_page.py`: `HostnamePage._resolve_or_diagnose`.
- [ ] `backend/webui_agent/login.py`: `start_keepalive`, `stop_keepalive`, `KEEPALIVE_INTERVAL_S`, `ensure_logged_in`.
- [ ] `backend/webui_agent/selectors/iosxe_default.yaml`: `dhcp_form` section (~lines 163–199) — no runtime **or** test reads it.

## B. Remove as a GROUP, with their tests (runtime-dead but test-coupled)
Deleting any one in isolation breaks CI (presence-assertion tests). Move code + YAML + tests together, or not at all:
- `login.py`: `is_session_expired`, the `role_text` `_build` branch.
- `iosxe_default.yaml`: `session_expired`, `nav`, `vlan_nav`, `vlan_form.cancel_button`.
- tests: `test_webui_login.py`, `test_webui_selectors.py`, `test_webui_vlan_page.py` presence assertions.

## C. Decide (design question, not just deletion)
- `backend/cli_agent/connection.py`: `ConnectionPool.close_all` — test-only. **Question:** should `main.py`'s FastAPI lifespan drain the SSH pool on shutdown (the missing caller)? Either wire it up or drop method + test.
- `tests/smoke/conftest.py`: `_smoke_results_dir` — `autouse` no-op (`return None`). Wire up its intended `artifacts/smoke/<ts>/` behavior or drop the stub.

## D. NOT dead — do not remove (verifier-confirmed live; listed to prevent re-flagging)
`ChatResponse.stop_reason` (live `/api/chat` field) · `RagNotInitialized` (raised at `retrieve.py:89`) · `run_ingest` (`python -m backend.knowledge_agent.ingest` entry) · `scripts/catalog_webui_elements.py` (CI-tested; `record_webui_catalog.py` is the live recorder).

## Caveat
`frontend-design-backup/` and `playwright_playground/` are documented in the kickoff/summary docs as **deliberately-retained "safety nets" pending a sweep** — known and intentional, not accidental. They stay as reference material until the vision driver is done; confirm the sweep is wanted before deleting.

*Re-run this audit anytime via the `dead-code-audit` skill.*
