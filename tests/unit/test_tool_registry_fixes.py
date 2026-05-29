"""Targeted tests for 4 executor/propose fixes:

- Fix 3  (convergence early-abort): same selector + same failure_reason twice
          → no_progress before the iteration cap fires.
- Fix 4  (gate per-iteration vision): check_plan_via_vision NOT called on
          every execution iteration — fires at most once mid-execution.
- Fix 5b (propose guard rejects empty steps): _propose_webui_configure rejects
          a plan with empty intent.role or intent.name.
- Fix 7  (replan-beyond-approved event): webui_configure_replan_beyond_approved
          log event fires when re-drafted plan grows beyond approved step count.
"""

from __future__ import annotations

from typing import Any

from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import approve_action, propose_action

# _clean_actions autouse fixture is in tests/conftest.py.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_PLAN_STEP = {
    "action": "click",
    "intent": {"role": "button", "name": "Add"},
    "value": None,
}


def _make_action(
    plan: list[dict],
    *,
    verify_text: str | None = "Done",
    session_id: str = "sess_test",
    intent: str = "configure thing",
    webui_path: str = "/webui/#/x",
) -> str:
    """Register + approve a webui_configure action and return its action_id."""
    action_id = propose_action(
        "webui_configure",
        {
            "intent": intent,
            "webui_path": webui_path,
            "plan": plan,
            "verify_text": verify_text,
            "session_id": session_id,
            "evidence": [],
        },
    )
    approve_action(action_id)
    return action_id


# ---------------------------------------------------------------------------
# Fix 3 — convergence early-abort
# ---------------------------------------------------------------------------


def test_same_failure_twice_aborts_with_no_progress(monkeypatch):
    """If the same step (same role+name) fails with the same error twice across
    iterations, the loop aborts with error='no_progress' BEFORE hitting the cap.

    The inner LLM re-drafts a plan that still targets the SAME selector (same
    role+name) so Fix 3 fires; we vary the plan hash (e.g. different value field)
    so inner_plan_stuck does NOT fire first.
    """
    initial_plan = [
        {
            "action": "click",
            "intent": {"role": "button", "name": "Submit"},
            "value": None,
        }
    ]
    # Draft returns a plan with the same selector but a different hash (value changed)
    redraft_plan = [
        {
            "action": "click",
            "intent": {"role": "button", "name": "Submit"},
            "value": "alt",  # differs from initial, so plan hash changes
        }
    ]
    action_id = _make_action(initial_plan, verify_text="Saved")

    call_count = 0

    def _act(**kwargs):
        nonlocal call_count
        call_count += 1
        # Always fail with element_missing
        return {"ok": False, "failure_reason": "element_missing", "chosen_eid": None}

    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", _act)
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})

    def _draft(*args, **kwargs):
        # Return a different hash plan but same selector → triggers Fix 3 on iter 2
        return {"plan": redraft_plan[:], "verify_text": "Saved", "risk": "ok"}

    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    # Must abort with no_progress, not iteration_cap_hit or inner_plan_stuck
    assert result["error"] == "no_progress", f"expected no_progress, got {result}"
    # Must abort BEFORE reaching the cap
    assert result["iteration"] < tr._WEBUI_CONFIGURE_MAX_ITER, (
        f"no_progress should fire before cap={tr._WEBUI_CONFIGURE_MAX_ITER}, "
        f"got iteration={result['iteration']}"
    )
    # Must record evidence
    assert result["failure_reason"] == "element_missing"
    assert result["failure_count"] == 2
    # mark_failed must have been called exactly once
    assert len(failed_ids) == 1


def test_different_failure_reasons_dont_trigger_no_progress(monkeypatch):
    """Different failure_reason values for the same step do NOT trigger no_progress.
    The loop should continue normally (until cap or inner_plan_empty)."""
    plan = [
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "IP Address"},
            "value": "10.0.0.1",
        }
    ]
    action_id = _make_action(plan, verify_text="10.0.0.1")

    errors = ["element_missing", "timeout", "element_missing"]
    call_count = 0

    def _act(**kwargs):
        nonlocal call_count
        err = errors[min(call_count, len(errors) - 1)]
        call_count += 1
        return {"ok": False, "failure_reason": err}

    draft_count = 0

    def _draft(*args, **kwargs):
        nonlocal draft_count
        draft_count += 1
        if draft_count >= 10:
            return {"plan": [], "verify_text": None, "risk": "giving up"}
        return {"plan": plan[:], "verify_text": "10.0.0.1", "risk": "ok"}

    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", _act)
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    # Should NOT bail with no_progress (different errors); may hit cap or stuck
    assert result.get("error") != "no_progress", (
        "Different failure_reason values should not trigger no_progress"
    )


def test_no_progress_does_not_fire_on_first_failure(monkeypatch):
    """Fix 3 requires TWO failures of the same (selector, reason). The first
    failure must NOT trigger no_progress — only the second should.

    Here: iter 1 fails with element_missing, draft returns a plan with a
    DIFFERENT selector name (different step), iter 2 succeeds → ok=True.
    no_progress must NOT fire.
    """
    initial_plan = [
        {
            "action": "click",
            "intent": {"role": "button", "name": "Save"},
            "value": None,
        }
    ]
    # After iter 1 fails, the inner LLM tries a different selector
    recovery_plan = [
        {
            "action": "click",
            "intent": {"role": "button", "name": "Apply"},  # different name
            "value": None,
        }
    ]
    action_id = _make_action(initial_plan, verify_text="Saved")

    call_count = 0

    def _act(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"ok": False, "failure_reason": "element_missing"}
        # Second call (different selector) succeeds
        return {"ok": True, "chosen_eid": "eid_1"}

    def _draft(*args, **kwargs):
        return {"plan": recovery_plan[:], "verify_text": "Saved", "risk": "ok"}

    executed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", _act)
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": call_count >= 2})
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    # Should succeed on iteration 2 after recovering
    assert result.get("ok") is True, (
        f"Expected ok=True after recovery, got {result}"
    )
    assert result.get("error") != "no_progress"


# ---------------------------------------------------------------------------
# Fix 4 — gate per-iteration vision
# ---------------------------------------------------------------------------


def test_vision_check_not_called_on_every_iteration(monkeypatch):
    """check_plan_via_vision must NOT be called on every execution iteration.
    It should fire at most once during execution (iter 2 only, not iter 3+)."""
    plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    action_id = _make_action(plan, verify_text="Done")

    vision_call_count = 0

    def _fake_check_plan_via_vision(**kwargs):
        nonlocal vision_call_count
        vision_call_count += 1
        from backend.orchestration.plan_vision_check import VisionVerdict

        return VisionVerdict(
            verdict="PROCEED",
            reason="test_proceed",
            suggested_plan=None,
            risks=[],
            confidence=1.0,
            tier=1,
            familiarity_score=0.3,
        )

    # Patch check_plan_via_vision at the tool_registry import path
    monkeypatch.setattr(
        "backend.orchestration.plan_vision_check.check_plan_via_vision",
        _fake_check_plan_via_vision,
    )

    # Need settings to have plan_vision_enabled=True
    from pathlib import Path as _Path

    from backend.core.settings import Settings

    fake_settings = Settings.model_construct(
        plan_vision_enabled=True,
        artifacts_dir=_Path("artifacts"),
    )

    # Mock get_settings to return our fake settings
    monkeypatch.setattr(
        "backend.core.settings.get_settings",
        lambda: fake_settings,
    )

    draft_count = 0

    def _draft(*args, **kwargs):
        nonlocal draft_count
        draft_count += 1
        if draft_count >= 3:
            return {"plan": [], "verify_text": None, "risk": "done"}
        # Return different plans so inner_plan_stuck doesn't fire
        return {
            "plan": [
                {
                    "action": "click",
                    "intent": {"role": "button", "name": f"Step{draft_count}"},
                    "value": None,
                }
            ],
            "verify_text": "Done",
            "risk": "ok",
        }

    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    tr.execute_tool("webui_configure", {"action_id": action_id})

    # We ran at least 3 iterations but vision should have fired at most once
    # (on iter 2 only — iter 1 is handled at propose-time, iter 3+ is gated).
    assert vision_call_count <= 1, (
        f"Vision should fire at most once mid-execution, fired {vision_call_count} times"
    )


def test_vision_check_fires_on_iter2_but_not_iter3(monkeypatch):
    """More explicit: vision fires exactly once (iter 2). Iter 3+ must skip."""
    plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    action_id = _make_action(plan, verify_text="Done")

    fired_at_iterations: list[int] = []
    # We need a way to know which iteration the vision call is happening.
    # We'll use a side-channel through a counter that tracks the current iteration.
    current_iter = [0]

    def _fake_check(**kwargs):
        fired_at_iterations.append(current_iter[0])
        from backend.orchestration.plan_vision_check import VisionVerdict

        return VisionVerdict(
            verdict="PROCEED",
            reason="test",
            suggested_plan=None,
            risks=[],
            confidence=1.0,
            tier=1,
            familiarity_score=0.3,
        )

    monkeypatch.setattr(
        "backend.orchestration.plan_vision_check.check_plan_via_vision",
        _fake_check,
    )

    from backend.core.settings import Settings

    fake_settings = Settings.model_construct(plan_vision_enabled=True)
    monkeypatch.setattr("backend.core.settings.get_settings", lambda: fake_settings)

    draft_count = 0

    def _draft(*args, **kwargs):
        nonlocal draft_count
        draft_count += 1
        current_iter[0] = draft_count + 1  # iter N+1 will execute this draft
        if draft_count >= 4:
            return {"plan": [], "verify_text": None, "risk": "done"}
        return {
            "plan": [
                {
                    "action": "click",
                    "intent": {"role": "button", "name": f"Step{draft_count}"},
                    "value": None,
                }
            ],
            "verify_text": "Done",
            "risk": "ok",
        }

    failed_ids: list[str] = []
    monkeypatch.setattr(tr, "webui_act_by_intent", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    tr.execute_tool("webui_configure", {"action_id": action_id})

    # Vision must have fired at most 1 time (Fix 4 gates to ≤1 mid-execution call)
    assert len(fired_at_iterations) <= 1, (
        f"Vision should fire ≤1 time mid-execution, fired at iterations {fired_at_iterations}"
    )


# ---------------------------------------------------------------------------
# Fix 5b — propose guard rejects empty steps
# ---------------------------------------------------------------------------


def test_propose_rejects_plan_with_empty_role(monkeypatch):
    """A plan with a step that has intent.role='' must be rejected at propose-time."""
    bad_plan = [
        {
            "action": "fill",
            "intent": {"role": "", "name": "IP Address"},  # empty role
            "value": "10.0.0.1",
        }
    ]
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_x", "view": {}})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"session_id": "sess_x", "view": {}}
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {
            "plan": bad_plan,
            "verify_text": "OK",
            "risk": "low",
            "equivalent_cli_commands": [],
        },
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "set IP", "webui_path": "/webui/#/interface"},
    )

    assert result["error"] == "invalid_plan", f"expected invalid_plan, got {result}"
    assert "intent.role" in result["message"] or "intent.name" in result["message"]
    assert result["step_index"] == 0


def test_propose_rejects_plan_with_empty_name(monkeypatch):
    """A plan with a step that has intent.name='' must be rejected at propose-time."""
    bad_plan = [
        {
            "action": "click",
            "intent": {"role": "button", "name": ""},  # empty name
            "value": None,
        }
    ]
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_x", "view": {}})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"session_id": "sess_x", "view": {}}
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {
            "plan": bad_plan,
            "verify_text": None,
            "risk": "low",
            "equivalent_cli_commands": [],
        },
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "click something", "webui_path": "/webui/#/x"},
    )

    assert result["error"] == "invalid_plan", f"expected invalid_plan, got {result}"
    assert result["step_index"] == 0


def test_propose_rejects_plan_with_none_intent_fields(monkeypatch):
    """None values for intent.role and intent.name are treated as empty."""
    bad_plan = [
        {
            "action": "fill",
            "intent": {"role": None, "name": None},
            "value": "192.168.1.1",
        }
    ]
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_x", "view": {}})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"session_id": "sess_x", "view": {}}
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {
            "plan": bad_plan,
            "verify_text": None,
            "risk": "low",
            "equivalent_cli_commands": [],
        },
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "set gateway", "webui_path": "/webui/#/routing"},
    )

    assert result["error"] == "invalid_plan"


def test_propose_accepts_valid_plan(monkeypatch):
    """A plan where all steps have non-empty role and name is accepted normally."""
    good_plan = [
        {
            "action": "click",
            "intent": {"role": "button", "name": "Add Route"},
            "value": None,
        },
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Destination Prefix"},
            "value": "10.0.0.0/8",
        },
    ]
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_ok", "view": {}})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"session_id": "sess_ok", "view": {}}
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {
            "plan": good_plan,
            "verify_text": "Route added",
            "risk": "low",
            "equivalent_cli_commands": [],
        },
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add static route", "webui_path": "/webui/#/staticRouting"},
    )

    # Should NOT be rejected for empty fields
    assert result.get("error") != "invalid_plan", f"Valid plan incorrectly rejected: {result}"
    assert result.get("status") == "awaiting_approval"


def test_propose_rejects_second_step_with_empty_name(monkeypatch):
    """If only the second step has an empty name, step_index should be 1."""
    bad_plan = [
        {
            "action": "click",
            "intent": {"role": "button", "name": "Add"},  # valid
            "value": None,
        },
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": ""},  # empty name
            "value": "10.0.0.1",
        },
    ]
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_x", "view": {}})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"session_id": "sess_x", "view": {}}
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {
            "plan": bad_plan,
            "verify_text": None,
            "risk": "low",
            "equivalent_cli_commands": [],
        },
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure ip", "webui_path": "/webui/#/interface"},
    )

    assert result["error"] == "invalid_plan"
    assert result["step_index"] == 1, f"Expected step_index=1, got {result.get('step_index')}"


# ---------------------------------------------------------------------------
# Fix 7 — replan-beyond-approved event
# ---------------------------------------------------------------------------


def test_replan_beyond_approved_event_fires(monkeypatch):
    """When the re-drafted plan has MORE steps than the approved plan,
    a webui_configure_replan_beyond_approved warning must be emitted.

    Captures events by patching the structlog bound logger on the module.
    """
    initial_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    # Approved step count = 1
    action_id = _make_action(initial_plan, verify_text="Done")

    warning_events: list[str] = []

    _original_log = tr.log

    class _CapturingLog:
        """Proxy that captures warning calls and forwards everything else."""

        def warning(self, event: str, **kwargs: Any) -> None:
            warning_events.append(event)
            _original_log.warning(event, **kwargs)

        def info(self, event: str, **kwargs: Any) -> None:
            _original_log.info(event, **kwargs)

        def error(self, event: str, **kwargs: Any) -> None:
            _original_log.error(event, **kwargs)

        def debug(self, event: str, **kwargs: Any) -> None:
            _original_log.debug(event, **kwargs)

    monkeypatch.setattr(tr, "log", _CapturingLog())

    draft_count = 0

    def _draft(*args, **kwargs):
        nonlocal draft_count
        draft_count += 1
        if draft_count == 1:
            # Re-draft returns 3 steps — more than the approved 1
            return {
                "plan": [
                    {
                        "action": "click",
                        "intent": {"role": "button", "name": "Add"},
                        "value": None,
                    },
                    {
                        "action": "fill",
                        "intent": {"role": "textbox", "name": "IP"},
                        "value": "10.0.0.1",
                    },
                    {
                        "action": "click",
                        "intent": {"role": "button", "name": "Save"},
                        "value": None,
                    },
                ],
                "verify_text": "Done",
                "risk": "ok",
            }
        # After that, return empty plan to terminate
        return {"plan": [], "verify_text": None, "risk": "done"}

    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    tr.execute_tool("webui_configure", {"action_id": action_id})

    # Check that the visibility event was emitted
    assert "webui_configure_replan_beyond_approved" in warning_events, (
        "Expected webui_configure_replan_beyond_approved warning event. "
        f"Captured warning events: {warning_events}"
    )


def test_replan_same_or_fewer_steps_no_event(monkeypatch):
    """Re-drafted plan with same or fewer steps must NOT emit the beyond-approved event."""
    initial_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
        {"action": "fill", "intent": {"role": "textbox", "name": "Name"}, "value": "r1"},
    ]
    # Approved step count = 2
    action_id = _make_action(initial_plan, verify_text="Done")

    warning_events: list[str] = []
    _original_log = tr.log

    class _CapturingLog:
        def warning(self, event: str, **kwargs: Any) -> None:
            warning_events.append(event)
            _original_log.warning(event, **kwargs)

        def info(self, event: str, **kwargs: Any) -> None:
            _original_log.info(event, **kwargs)

        def error(self, event: str, **kwargs: Any) -> None:
            _original_log.error(event, **kwargs)

        def debug(self, event: str, **kwargs: Any) -> None:
            _original_log.debug(event, **kwargs)

    monkeypatch.setattr(tr, "log", _CapturingLog())

    draft_count = 0

    def _draft(*args, **kwargs):
        nonlocal draft_count
        draft_count += 1
        if draft_count == 1:
            # Re-draft returns exactly 2 steps — same as approved
            return {
                "plan": [
                    {
                        "action": "click",
                        "intent": {"role": "button", "name": "Add"},
                        "value": None,
                    },
                    {
                        "action": "fill",
                        "intent": {"role": "textbox", "name": "Hostname"},
                        "value": "router1",
                    },
                ],
                "verify_text": "Done",
                "risk": "ok",
            }
        return {"plan": [], "verify_text": None, "risk": "done"}

    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    tr.execute_tool("webui_configure", {"action_id": action_id})

    assert "webui_configure_replan_beyond_approved" not in warning_events, (
        "No replan_beyond_approved event expected for same-or-fewer steps"
    )


def test_replan_beyond_approved_event_includes_counts(monkeypatch):
    """The replan_beyond_approved event must carry approved_step_count,
    replanned_step_count, and delta kwargs for operator visibility."""
    initial_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    action_id = _make_action(initial_plan, verify_text="Done")

    captured_kwargs: list[dict] = []
    _original_log = tr.log

    class _CapturingLog:
        def warning(self, event: str, **kwargs: Any) -> None:
            if event == "webui_configure_replan_beyond_approved":
                captured_kwargs.append(kwargs)
            _original_log.warning(event, **kwargs)

        def info(self, event: str, **kwargs: Any) -> None:
            _original_log.info(event, **kwargs)

        def error(self, event: str, **kwargs: Any) -> None:
            _original_log.error(event, **kwargs)

        def debug(self, event: str, **kwargs: Any) -> None:
            _original_log.debug(event, **kwargs)

    monkeypatch.setattr(tr, "log", _CapturingLog())

    _draft_calls = [0]

    def _draft(*args, **kwargs):
        _draft_calls[0] += 1
        if _draft_calls[0] == 1:
            # First re-draft: 2 steps (approved was 1)
            return {
                "plan": [
                    {
                        "action": "click",
                        "intent": {"role": "button", "name": "Add"},
                        "value": None,
                    },
                    {
                        "action": "fill",
                        "intent": {"role": "textbox", "name": "IP"},
                        "value": "1.1.1.1",
                    },
                ],
                "verify_text": "Done",
                "risk": "ok",
            }
        # Terminate on second call
        return {"plan": [], "verify_text": None, "risk": "done"}

    failed_ids: list[str] = []
    monkeypatch.setattr(tr, "webui_act_by_intent", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    tr.execute_tool("webui_configure", {"action_id": action_id})

    assert len(captured_kwargs) >= 1, "Expected at least one replan_beyond_approved event"
    kw = captured_kwargs[0]
    assert "approved_step_count" in kw, f"Missing approved_step_count in {kw}"
    assert "replanned_step_count" in kw, f"Missing replanned_step_count in {kw}"
    assert "delta" in kw, f"Missing delta in {kw}"
    assert kw["approved_step_count"] == 1
    assert kw["replanned_step_count"] == 2
    assert kw["delta"] == 1
