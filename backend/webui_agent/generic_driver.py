"""AI-driven generic driver for the Cisco IOS XE WebUI.

Phase 4 of the AI-first WebUI v0.4.0 plan (see
[docs/plan-ai-first-webui.md](../../docs/plan-ai-first-webui.md)).
Exposes JSON-returning tools that the planner (Claude Haiku 4.5) calls
to navigate, observe, and act on the Cisco WebUI without us hand-coding
a Page Object per feature.

Slice 1 (this file) contains only `webui_open`:
    - Spawns a long-lived `WebUISession` via the Playwright child process.
    - Navigates to the requested path.
    - Returns the semantic-DOM view from `describe_page`.
    - Caches the session on `action_id` so subsequent ops (slice 2's
      `webui_act` / `webui_describe_page` / `webui_verify`) reuse it
      without paying the ~5-20 s Cisco WebUI login cost again.

Slice 2 adds the action tools (`webui_act`, `webui_act_by_intent`,
`webui_describe_page`, `webui_verify`) on top of the same session
machinery.

Lifetime: each session is intended for ONE planner turn. The orchestrator
should call `close_all_sessions()` when a turn ends. The atexit hook
catches any leftover sessions on process shutdown — Chromium child
processes are expensive to leak on Windows.

HITL: `webui_open` is read-only (a navigation, no router write). NOT in
`_REQUIRES_APPROVAL`. Slice 2's action tools register there.
"""

from __future__ import annotations

import atexit
import threading
import uuid
from typing import Any

from backend.core.logging import get_logger
from backend.webui_agent._subprocess import SubprocessFlowError, WebUISession

log = get_logger(__name__)


# Module-level session cache, keyed on the action_id passed to `webui_open`.
# A lock guards the dict against the rare case of two FastAPI threads
# spawning sessions for the same action_id concurrently.
_sessions: dict[str, WebUISession] = {}
_sessions_lock = threading.Lock()


def webui_open(
    path: str,
    action_id: str | None = None,
    headless: bool | None = None,
) -> dict[str, Any]:
    """Navigate the Cisco WebUI to ``path`` and return a semantic view.

    Read-only. Reuses an existing session for the same ``action_id`` if
    one is alive; otherwise spawns a new subprocess and completes login.

    Args:
        path: URL or path the child should navigate to (e.g.
            ``"/webui/#/general"`` — hash-fragment routing skips the
            Cisco sidebar walk).
        action_id: planner-turn key. If ``None``, a fresh ``sess_<8hex>``
            id is allocated and returned in ``session_id``.
        headless: passed through to the child's browser launch. ``None``
            falls back to the env-var resolution in
            [browser.py](browser.py).

    Returns:
        Success: ``{"view": <semantic_dom output>, "session_id": str}``.
        Init failure: ``{"error": "session_init_failed", "message": str,
            "exc_type": str, "session_id": str}``.
        Op failure: ``{"error": "webui_open_failed", "message": str,
            "exc_type": str, "session_id": str}``.
    """
    session_id = action_id or f"sess_{uuid.uuid4().hex[:8]}"
    try:
        sess = _get_or_create_session(session_id, headless=headless)
    except SubprocessFlowError as exc:
        log.error(
            "webui_session_init_failed",
            session_id=session_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "session_init_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    try:
        reply = sess.send({"op": "open", "path": path})
    except SubprocessFlowError as exc:
        # Subprocess crashed mid-op — discard the dead session so the
        # next call rebuilds a clean one.
        _close_session(session_id)
        log.error(
            "webui_open_subprocess_error",
            session_id=session_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_open_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        # Child reported a clean failure (e.g. navigation timed out, page
        # didn't render). Keep the session — the planner can retry.
        log.warning(
            "webui_open_reply_not_ok",
            session_id=session_id,
            error=reply.get("error"),
        )
        return {
            "error": "webui_open_failed",
            "message": str(reply.get("error", "open failed")),
            "exc_type": str(reply.get("exc_type", "Unknown")),
            "session_id": session_id,
        }

    return {"view": reply["view"], "session_id": session_id}


def _get_or_create_session(
    session_id: str,
    *,
    headless: bool | None,
) -> WebUISession:
    """Return a live session for ``session_id``, building one if needed."""
    with _sessions_lock:
        existing = _sessions.get(session_id)
        if existing is not None and existing.is_alive():
            return existing
        if existing is not None:
            # Stale handle to a dead subprocess — drop before rebuilding.
            _sessions.pop(session_id, None)
        new_sess = WebUISession(session_id, headless=headless)
        _sessions[session_id] = new_sess
        return new_sess


def _close_session(session_id: str) -> None:
    with _sessions_lock:
        sess = _sessions.pop(session_id, None)
    if sess is not None:
        try:
            sess.close()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "session_close_error",
                session_id=session_id,
                error=str(exc),
            )


def close_all_sessions() -> None:
    """Close every cached session. Idempotent.

    Called by the orchestrator when a planner turn ends, and as an
    atexit hook on process shutdown.
    """
    with _sessions_lock:
        ids = list(_sessions.keys())
    for sid in ids:
        _close_session(sid)


atexit.register(close_all_sessions)
