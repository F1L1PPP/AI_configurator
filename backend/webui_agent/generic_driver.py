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

from backend.cli_agent.connection import pool
from backend.cli_agent.snapshots import take_snapshot
from backend.core.logging import get_logger
from backend.core.settings import get_settings
from backend.orchestration.confirmations import is_approved, mark_failed
from backend.webui_agent._subprocess import SubprocessFlowError, WebUISession

log = get_logger(__name__)


# Module-level session cache, keyed on the action_id passed to `webui_open`.
# A lock guards the dict against the rare case of two FastAPI threads
# spawning sessions for the same action_id concurrently.
_sessions: dict[str, WebUISession] = {}
_sessions_lock = threading.Lock()

# Action_ids that have already had their pre-snapshot taken. Phase 4 design
# moved pre-snap from "first webui_act" into "first webui_open with an
# action_id" — so pre-snap exists even if the very first act crashes
# before any post-snap could be taken. This set is the dedupe guard so we
# don't re-snap when the same action_id reuses the cached session for
# multiple navigations. Cleared per-test by the autouse fixture in
# tests/unit/test_generic_driver.py.
_pre_snapshotted: set[str] = set()

# Vision-selector failure registry.
#
# Vision eids (format: "vision_<hex>") are synthetic keys emitted by
# describe_page when Playwright's accessibility-tree lookup falls back to
# a coordinate-based / screenshot-derived locator strategy. They are
# session-scoped and may not survive DOM mutations — a form re-render,
# Angular digest, or page navigation can silently invalidate them.
#
# When a webui_act / webui_act_by_intent call returns
# failure_reason="element_missing" for a vision eid, that eid is added to
# this set for the lifetime of the session.  Any subsequent call that
# re-presents the same (session_id, eid) pair is immediately refused with
# failure_reason="element_missing" — no round-trip to the child, no
# 4–5 s Playwright timeout, no silent re-resolve.  Using the same reason
# keeps the convergence guard in tool_registry.py tripping on the same
# signal in 2 iterations (it keys on failure_reason).
#
# Shape: { session_id: {vision_eid, ...} }
# Cleared per-test by the autouse fixture in tests/unit/test_generic_driver.py.
_vision_eid_failures: dict[str, set[str]] = {}
_vision_eid_failures_lock = threading.Lock()


def _is_vision_eid(eid: str) -> bool:
    """Return True if ``eid`` was produced by a vision/fallback strategy.

    describe_page emits vision eids with the prefix ``vision_`` when it
    cannot find a stable ARIA-rooted locator.  These are the only eids
    subject to eviction — stable numeric eids (``e_NNN``) from the
    accessibility tree survive DOM mutations and are not evicted.
    """
    return eid.startswith("vision_")


def _record_vision_failure(session_id: str, eid: str) -> None:
    """Persist a vision-eid element_missing failure for ``session_id``."""
    with _vision_eid_failures_lock:
        _vision_eid_failures.setdefault(session_id, set()).add(eid)


def _is_vision_eid_evicted(session_id: str, eid: str) -> bool:
    """Return True if ``eid`` was previously evicted for ``session_id``."""
    with _vision_eid_failures_lock:
        return eid in _vision_eid_failures.get(session_id, set())


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

    # Pre-snapshot for any real action_id (skipped for the auto-generated
    # sess_XXXX ids, which represent read-only navigations the planner
    # isn't tracking). Best-effort — take_snapshot raises on SSH failure
    # but we don't want pre-snap failure aborting the WebUI flow.
    _maybe_pre_snapshot(action_id)

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


def webui_describe_page(session_id: str) -> dict[str, Any]:
    """Re-describe the current page of an existing session.

    Read-only. Returns the fresh semantic-DOM view (with a new
    `view_id`); the locator_map is rebuilt child-side. The caller
    (Phase 5's planner wrapper) must drop any cached eid references
    after this call — they were tied to the previous view_id.
    """
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        return _session_not_found(session_id)

    try:
        reply = sess.send({"op": "describe"})
    except SubprocessFlowError as exc:
        _close_session(session_id)
        log.error(
            "webui_describe_subprocess_error",
            session_id=session_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_describe_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        return {
            "error": "webui_describe_failed",
            "message": str(reply.get("error", "describe failed")),
            "exc_type": str(reply.get("exc_type", "Unknown")),
            "session_id": session_id,
        }

    return {"view": reply["view"], "session_id": session_id}


def webui_verify(session_id: str, text: str) -> dict[str, Any]:
    """Check whether ``text`` appears in the current page's HTML.

    Read-only post-condition check. Use after a `webui_act` chain to
    confirm a success banner or expected value rendered. Returns
    ``{"present": bool, "url": str, "session_id": str}`` on success.
    """
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        return _session_not_found(session_id)

    try:
        reply = sess.send({"op": "verify", "text": text})
    except SubprocessFlowError as exc:
        _close_session(session_id)
        log.error(
            "webui_verify_subprocess_error",
            session_id=session_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_verify_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        return {
            "error": "webui_verify_failed",
            "message": str(reply.get("error", "verify failed")),
            "exc_type": str(reply.get("exc_type", "Unknown")),
            "session_id": session_id,
        }

    return {
        "present": bool(reply.get("present", False)),
        "url": str(reply.get("url", "")),
        "session_id": session_id,
    }


def webui_act(
    session_id: str,
    view_id: str,
    eid: str,
    action: str,
    action_id: str,
    value: str | None = None,
) -> dict[str, Any]:
    """Act on a previously-described element. HITL-gated write tool.

    The `view_id` ties this call to a specific describe — the child rejects
    the act with `failure_reason="stale_view"` if its current_view_id has
    rolled past this one (e.g. a re-describe happened between the planner
    reading the view and issuing this call).

    Returns:
        Success: ``{"ok": True, "view": ..., "attempts": int, "evidence":
            {"screenshot_dir": str}, "session_id": str}``.
        Soft failure (planner can retry): ``{"ok": False, "failure_reason":
            "<one of stale_view|unknown_eid|unknown_action|click_timeout_
            unsafe_retry|element_missing|element_hidden|element_disabled|
            element_intercepted|unknown_error>", "view": ..., "attempts":
            int, "session_id": str}``. `mark_failed` is NOT called — the
            action stays APPROVED/EXECUTING so the planner can retry.
        Hard failure (subprocess died / not_approved): ``{"error":
            "<not_approved|session_not_found|webui_act_failed>", ...}``.
            `mark_failed(action_id)` is called for subprocess crashes and
            session_not_found (no session = state unrecoverable).

    Does NOT call `mark_executed`. The multi-act flow needs the action to
    stay in EXECUTING so subsequent acts pass `is_approved`. `mark_executed`
    is Phase 5's `propose_webui_configure` wrapper's responsibility.
    """
    # HITL layer 2 — re-check before any side effect.
    if not is_approved(action_id):
        log.info("webui_act_not_approved", action_id=action_id)
        return {
            "error": "not_approved",
            "message": f"action_id {action_id!r} is not APPROVED",
            "session_id": session_id,
        }

    # Vision-eid eviction guard — refuse immediately if this eid was
    # previously evicted for this session (element_missing on a vision
    # selector). No child round-trip, no Playwright timeout. Return the
    # same failure_reason="element_missing" so the convergence guard in
    # tool_registry.py trips on the same signal in 2 iterations.
    if _is_vision_eid(eid) and _is_vision_eid_evicted(session_id, eid):
        log.info(
            "webui_act_vision_eid_evicted",
            session_id=session_id,
            action_id=action_id,
            eid=eid,
        )
        return {
            "ok": False,
            "failure_reason": "element_missing",
            "chosen_eid": eid,
            "view": None,
            "attempts": 0,
            "session_id": session_id,
        }

    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        # No session means we can't proceed; the state machine considers
        # this a hard failure.
        mark_failed(action_id)
        return _session_not_found(session_id)

    try:
        reply = sess.send(
            {
                "op": "act",
                "view_id": view_id,
                "eid": eid,
                "action": action,
                "value": value,
            }
        )
    except SubprocessFlowError as exc:
        _close_session(session_id)
        mark_failed(action_id)
        log.error(
            "webui_act_subprocess_error",
            session_id=session_id,
            action_id=action_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_act_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        failure_reason = reply.get("failure_reason")
        # When a vision eid fails element_missing, evict it immediately so
        # no future call to webui_act wastes Playwright timeout budget on it.
        if failure_reason == "element_missing" and _is_vision_eid(eid):
            _record_vision_failure(session_id, eid)
            log.info(
                "webui_act_vision_eid_recorded",
                session_id=session_id,
                action_id=action_id,
                eid=eid,
            )
        # Soft failure — surface the failure_reason to the caller without
        # transitioning the action_id. The planner re-describes and retries.
        log.info(
            "webui_act_soft_failure",
            session_id=session_id,
            action_id=action_id,
            failure_reason=failure_reason,
        )
        return {
            "ok": False,
            "failure_reason": failure_reason,
            "view": reply.get("view"),
            "attempts": reply.get("attempts", 0),
            "session_id": session_id,
        }

    # Success — the act may have mutated the router (e.g. clicking Apply).
    # Invalidate the SSH pool so the next CLI tool call sees a fresh
    # connection. Pattern mirrors flows/change_hostname.py:101.
    settings = get_settings()
    pool.invalidate(settings.router_host, settings.router_ssh_user)
    log.info(
        "webui_act_complete",
        session_id=session_id,
        action_id=action_id,
        eid=eid,
        action=action,
        attempts=reply.get("attempts", 0),
    )
    return {
        "ok": True,
        "view": reply["view"],
        "evidence": reply.get("evidence", {}),
        "session_id": session_id,
        "attempts": reply.get("attempts", 0),
    }


def webui_act_by_intent(
    session_id: str,
    intent: dict[str, Any],
    action_id: str,
) -> dict[str, Any]:
    """Act on an element described by intent ({role, name, action, value}).

    Convenience wrapper around the child's intent resolver — the planner
    states what it wants ("click the Apply button"), the child uses
    `login.first_match` to resolve the live Locator, reverse-looks-up
    the eid against a fresh describe, and dispatches into the same
    `_do_act` self-heal machine as `webui_act`.

    Returns:
        Same shape as `webui_act` plus a `chosen_eid` field that names
        the eid the child resolved the intent to (or ``None`` if the
        resolution failed).

    HITL + pool invalidation + mark_failed semantics are identical to
    `webui_act`. Does NOT call `mark_executed`.
    """
    # HITL layer 2 — re-check before any side effect.
    if not is_approved(action_id):
        log.info("webui_act_by_intent_not_approved", action_id=action_id)
        return {
            "error": "not_approved",
            "message": f"action_id {action_id!r} is not APPROVED",
            "session_id": session_id,
        }

    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        mark_failed(action_id)
        return _session_not_found(session_id)

    try:
        reply = sess.send({"op": "act_by_intent", "intent": intent})
    except SubprocessFlowError as exc:
        _close_session(session_id)
        mark_failed(action_id)
        log.error(
            "webui_act_by_intent_subprocess_error",
            session_id=session_id,
            action_id=action_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_act_by_intent_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        failure_reason = reply.get("failure_reason")
        chosen_eid = reply.get("chosen_eid")
        # When the child resolved the intent to a vision eid and that eid
        # failed element_missing, evict it so future direct webui_act calls
        # with the same eid are refused immediately.
        if (
            failure_reason == "element_missing"
            and isinstance(chosen_eid, str)
            and _is_vision_eid(chosen_eid)
        ):
            _record_vision_failure(session_id, chosen_eid)
            log.info(
                "webui_act_by_intent_vision_eid_recorded",
                session_id=session_id,
                action_id=action_id,
                chosen_eid=chosen_eid,
            )
        log.info(
            "webui_act_by_intent_soft_failure",
            session_id=session_id,
            action_id=action_id,
            failure_reason=failure_reason,
            chosen_eid=chosen_eid,
        )
        return {
            "ok": False,
            "failure_reason": failure_reason,
            "chosen_eid": chosen_eid,
            "view": reply.get("view"),
            "attempts": reply.get("attempts", 0),
            "session_id": session_id,
        }

    # Success — pool.invalidate like webui_act.
    settings = get_settings()
    pool.invalidate(settings.router_host, settings.router_ssh_user)
    log.info(
        "webui_act_by_intent_complete",
        session_id=session_id,
        action_id=action_id,
        chosen_eid=reply.get("chosen_eid"),
        attempts=reply.get("attempts", 0),
    )
    return {
        "ok": True,
        "view": reply["view"],
        "chosen_eid": reply.get("chosen_eid"),
        "evidence": reply.get("evidence", {}),
        "session_id": session_id,
        "attempts": reply.get("attempts", 0),
    }


# ---------------------------------------------------------------------------
# C3: Atlas act-path wrappers (ADDITIVE — do NOT modify existing functions)
# ---------------------------------------------------------------------------


def webui_perceive(
    session_id: str,
    route: str | None = None,
    device_fingerprint: str = "unknown__unknown",
) -> dict[str, Any]:
    """Observe the current page via atlas + accessibility tree.

    Read-only — no HITL gate required.

    Returns:
        ``{"view": ..., "drift": bool, "captured": bool,
           "missing_required": [...], "unmapped_fields": [...],
           "session_id": str}`` on success.
        ``{"error": ..., "session_id": str}`` on failure.
    """
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        return _session_not_found(session_id)

    try:
        reply = sess.send(
            {
                "op": "perceive",
                "route": route,
                "device_fingerprint": device_fingerprint,
            }
        )
    except SubprocessFlowError as exc:
        _close_session(session_id)
        log.error(
            "webui_perceive_subprocess_error",
            session_id=session_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_perceive_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        return {
            "error": "webui_perceive_failed",
            "message": str(reply.get("error", "perceive failed")),
            "exc_type": str(reply.get("exc_type", "Unknown")),
            "session_id": session_id,
        }

    return {
        "view": reply["view"],
        "drift": reply.get("drift", False),
        "captured": reply.get("captured", False),
        "missing_required": reply.get("missing_required", []),
        "unmapped_fields": reply.get("unmapped_fields", []),
        "session_id": session_id,
    }


def webui_act_field(
    session_id: str,
    field_key: str,
    value: Any,
    action_id: str,
) -> dict[str, Any]:
    """Apply a value to a single atlas-mapped field. HITL-gated write tool.

    Returns:
        Success: ``{"ok": True, "field_key": str, "attempts": int,
            "session_id": str}``.
        Soft failure: ``{"ok": False, "failure_reason": str,
            "field_key": str, "session_id": str}`` — mark_failed NOT called;
            orchestrator may retry.
        Hard failure: ``{"error": ..., "session_id": str}`` — mark_failed
            called for subprocess crashes / missing session.
    """
    if not is_approved(action_id):
        log.info("webui_act_field_not_approved", action_id=action_id)
        return {
            "error": "not_approved",
            "message": f"action_id {action_id!r} is not APPROVED",
            "session_id": session_id,
        }

    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        mark_failed(action_id)
        return _session_not_found(session_id)

    try:
        reply = sess.send(
            {
                "op": "act_field",
                "field_key": field_key,
                "value": value,
            }
        )
    except SubprocessFlowError as exc:
        _close_session(session_id)
        mark_failed(action_id)
        log.error(
            "webui_act_field_subprocess_error",
            session_id=session_id,
            action_id=action_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_act_field_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        failure_reason = reply.get("failure_reason")
        log.info(
            "webui_act_field_soft_failure",
            session_id=session_id,
            action_id=action_id,
            field_key=field_key,
            failure_reason=failure_reason,
        )
        # Soft failure — do NOT mark_failed; orchestrator may retry.
        return {
            "ok": False,
            "failure_reason": failure_reason,
            "field_key": reply.get("field_key", field_key),
            "session_id": session_id,
        }

    settings = get_settings()
    pool.invalidate(settings.router_host, settings.router_ssh_user)
    log.info(
        "webui_act_field_complete",
        session_id=session_id,
        action_id=action_id,
        field_key=field_key,
        attempts=reply.get("attempts", 0),
    )
    return {
        "ok": True,
        "field_key": reply.get("field_key", field_key),
        "attempts": reply.get("attempts", 0),
        "session_id": session_id,
    }


def webui_apply_control(
    session_id: str,
    action_id: str,
    key: str | None = None,
) -> dict[str, Any]:
    """Click the atlas apply control (router write). HITL-gated.

    CLAUDE.md §4: NEVER retried on TimeoutError.

    Returns:
        Success: ``{"ok": True, "session_id": str}``.
        Soft failure: ``{"ok": False, "failure_reason": str,
            "session_id": str}`` — mark_failed NOT called.
        Hard failure: ``{"error": ..., "session_id": str}`` — mark_failed
            called for subprocess crashes / missing session.
    """
    if not is_approved(action_id):
        log.info("webui_apply_control_not_approved", action_id=action_id)
        return {
            "error": "not_approved",
            "message": f"action_id {action_id!r} is not APPROVED",
            "session_id": session_id,
        }

    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        mark_failed(action_id)
        return _session_not_found(session_id)

    try:
        reply = sess.send({"op": "apply_control", "key": key})
    except SubprocessFlowError as exc:
        _close_session(session_id)
        mark_failed(action_id)
        log.error(
            "webui_apply_control_subprocess_error",
            session_id=session_id,
            action_id=action_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_apply_control_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        failure_reason = reply.get("failure_reason")
        log.info(
            "webui_apply_control_soft_failure",
            session_id=session_id,
            action_id=action_id,
            failure_reason=failure_reason,
        )
        return {
            "ok": False,
            "failure_reason": failure_reason,
            "session_id": session_id,
        }

    settings = get_settings()
    pool.invalidate(settings.router_host, settings.router_ssh_user)
    log.info(
        "webui_apply_control_complete",
        session_id=session_id,
        action_id=action_id,
    )
    return {"ok": True, "session_id": session_id}


def webui_verify_a11y(
    session_id: str,
    contains: str,
) -> dict[str, Any]:
    """Check whether ``contains`` appears in the live accessibility tree.

    Read-only — no HITL gate.

    Returns:
        ``{"present": bool, "session_id": str}`` on success.
        ``{"error": ..., "session_id": str}`` on failure.
    """
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        return _session_not_found(session_id)

    try:
        reply = sess.send({"op": "verify_a11y", "contains": contains})
    except SubprocessFlowError as exc:
        _close_session(session_id)
        log.error(
            "webui_verify_a11y_subprocess_error",
            session_id=session_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "webui_verify_a11y_failed",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        return {
            "error": "webui_verify_a11y_failed",
            "message": str(reply.get("error", "verify_a11y failed")),
            "exc_type": str(reply.get("exc_type", "Unknown")),
            "session_id": session_id,
        }

    return {
        "present": bool(reply.get("present", False)),
        "session_id": session_id,
    }


def _session_not_found(session_id: str) -> dict[str, Any]:
    return {
        "error": "session_not_found",
        "message": f"no live session for session_id={session_id!r}",
        "session_id": session_id,
    }


def _maybe_pre_snapshot(action_id: str | None) -> None:
    """Take pre-snapshot for an action_id once (best-effort).

    Skipped for auto-generated sess_XXXX ids (no real action_id behind
    them — they're throwaway read-only navigations). Skipped if this
    action_id already had its pre-snap. Snapshot failure is logged but
    NOT raised — pre-snap is evidence, not a flow precondition.
    """
    if action_id is None:
        return
    with _sessions_lock:
        if action_id in _pre_snapshotted:
            return
        _pre_snapshotted.add(action_id)
    # Lock released before SSH I/O — take_snapshot can take seconds.
    try:
        take_snapshot(action_id, "pre")
        log.info("webui_open_pre_snapshot_taken", action_id=action_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "webui_open_pre_snapshot_failed",
            action_id=action_id,
            error=str(exc),
        )


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


def webui_open_form_for_planning(
    session_id: str,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Perform a single form-opening click in a propose-time session WITHOUT an approval gate.

    SECURITY RATIONALE — why this is safe without ``is_approved``:
      - This helper is ONLY called during ``_propose_webui_configure``, before
        any ``action_id`` exists (``propose_action`` has not run yet).
      - It is intentionally constrained to a SINGLE CLICK that opens a modal
        or form on the current page — it must never be used for fills, selects,
        or submits.  The caller enforces this by only passing an intent whose
        ``action`` is ``"click"``.  Any non-click action supplied here is
        rejected immediately (no subprocess round-trip).
      - Opening a modal is a READ-ONLY navigation from the router's perspective:
        no router config change is committed, no XHR write lands at the device.
        The actual writes ("Apply to Device", "Save") remain plan steps that
        are gated by the full ``propose_action → approve → execute`` flow.
      - This function MUST NOT be exposed as a public tool and MUST NOT be
        called from any HITL-approved execute path.  Its sole purpose is to
        reveal the real form so ``draft_plan`` plans against actual field names
        instead of hallucinating them from the list-view.

    Args:
        session_id: The live session opened by ``webui_open`` inside
            ``_propose_webui_configure``.  Must already be alive.
        intent: Dict with keys ``role``, ``name``, ``action`` (must be
            ``"click"``), and optionally ``value`` (ignored for clicks).

    Returns:
        ``{"ok": True, "view": ..., "session_id": str}`` on success.
        ``{"error": "<reason>", ...}`` on any failure — caller should soft-fail
        and continue with the original list-view plan.
    """
    # Hard constraint: this helper performs ONLY clicks.  Anything else is
    # a programming error in the caller — refuse loudly so tests catch it.
    if intent.get("action") != "click":
        log.error(
            "webui_open_form_for_planning_non_click_refused",
            session_id=session_id,
            action=intent.get("action"),
        )
        return {
            "error": "non_click_refused",
            "message": (
                "webui_open_form_for_planning only accepts action='click'. "
                f"Got action={intent.get('action')!r}."
            ),
            "session_id": session_id,
        }

    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None or not sess.is_alive():
        return _session_not_found(session_id)

    log.info(
        "webui_open_form_for_planning_click",
        session_id=session_id,
        intent_role=intent.get("role"),
        intent_name=intent.get("name"),
    )

    try:
        reply = sess.send({"op": "act_by_intent", "intent": intent})
    except SubprocessFlowError as exc:
        _close_session(session_id)
        log.error(
            "webui_open_form_for_planning_subprocess_error",
            session_id=session_id,
            exc_type=exc.exc_type,
            error=exc.error,
        )
        return {
            "error": "open_form_subprocess_error",
            "message": exc.error,
            "exc_type": exc.exc_type,
            "session_id": session_id,
        }

    if not reply.get("ok"):
        log.warning(
            "webui_open_form_for_planning_click_failed",
            session_id=session_id,
            failure_reason=reply.get("failure_reason"),
        )
        return {
            "error": "open_form_click_failed",
            "failure_reason": reply.get("failure_reason"),
            "view": reply.get("view"),
            "session_id": session_id,
        }

    return {
        "ok": True,
        "view": reply.get("view"),
        "session_id": session_id,
    }
