"""Unit tests for Phase 5 tool_registry additions:
- propose_webui_configure
- webui_configure
- Confirm low-level tools removed from TOOL_SCHEMAS
"""

from __future__ import annotations

import httpx
from anthropic._exceptions import OverloadedError as AnthropicOverloadedError

from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import (
    approve_action,
    propose_action,
)

# _clean_actions autouse fixture is in tests/conftest.py.


# ---------------------------------------------------------------------------
# Schema membership assertions
# ---------------------------------------------------------------------------


def test_low_level_tools_removed_from_schemas():
    """webui_act, webui_act_by_intent, webui_open must NOT be in TOOL_SCHEMAS."""
    schema_names = {s["name"] for s in tr.TOOL_SCHEMAS}
    assert "webui_act" not in schema_names
    assert "webui_act_by_intent" not in schema_names
    assert "webui_open" not in schema_names
    assert "webui_describe_page" not in schema_names
    assert "webui_verify" not in schema_names


def test_new_tools_present_in_schemas():
    schema_names = {s["name"] for s in tr.TOOL_SCHEMAS}
    assert "propose_webui_configure" in schema_names
    assert "webui_configure" in schema_names


def test_webui_configure_in_write_tools():
    assert "webui_configure" in tr.WRITE_TOOLS


def test_old_generic_write_tools_not_in_write_tools():
    assert "webui_act" not in tr.WRITE_TOOLS
    assert "webui_act_by_intent" not in tr.WRITE_TOOLS


# ---------------------------------------------------------------------------
# propose_webui_configure — happy path
# ---------------------------------------------------------------------------


def test_propose_webui_configure_happy_path(monkeypatch):
    """Mock search_docs, webui_open, webui_describe_page, draft_plan;
    assert action_id returned and plan stored in params."""
    rag_result = {
        "results": [
            {"text": "OSPF config info", "source": "ospf.pdf", "section": "OSPF Basics"},
        ]
    }
    open_result = {"session_id": "sess_abc123", "view": {"elements": []}}
    desc_result = {
        "session_id": "sess_abc123",
        "view": {"elements": [{"role": "button", "name": "Add Process"}]},
    }
    drafted = {
        "plan": [
            {
                "action": "click",
                "intent": {"role": "button", "name": "Add Process"},
                "value": None,
            }
        ],
        "verify_text": "OSPF process 100",
        "risk": "Enabling OSPF may affect routing.",
    }

    monkeypatch.setattr(tr, "_search_docs", lambda **kw: rag_result)
    monkeypatch.setattr(tr, "webui_open", lambda **kw: open_result)
    monkeypatch.setattr(tr, "webui_describe_page", lambda **kw: desc_result)
    monkeypatch.setattr(tr, "draft_plan", lambda *a, **kw: drafted)
    # desc_result has "Add Process" button (no textboxes) → heuristic fires; stub
    # the form-open so no real session is touched.  Returning not-ok means the
    # heuristic falls back to the list view and the single authoritative draft_plan
    # call still produces the expected plan.
    monkeypatch.setattr(
        tr, "webui_open_form_for_planning", lambda sid, intent: {"ok": False, "failure_reason": "no_session"}
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure OSPF process 100 area 0", "webui_path": "/webui/#/routing/ospf"},
    )

    assert result["status"] == "awaiting_approval"
    assert result["action_id"].startswith("act_")
    assert result["execute_tool"] == "webui_configure"
    assert result["preview"]["step_count"] == 1
    assert result["preview"]["plan"][0]["action"] == "click"
    assert result["preview"]["verify_text"] == "OSPF process 100"


# ---------------------------------------------------------------------------
# propose_webui_configure — empty plan (intent not mappable)
# ---------------------------------------------------------------------------


def test_propose_webui_configure_empty_plan_returns_error(monkeypatch):
    """Inner LLM returns empty plan → error: intent_not_mappable."""
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
            "plan": [],
            "verify_text": None,
            "risk": "Cannot map intent to current view: OSPF panel not visible",
        },
    )

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure OSPF", "webui_path": "/webui/#/routing/ospf"},
    )

    assert result["error"] == "intent_not_mappable"
    assert "Cannot map" in result["message"]


# ---------------------------------------------------------------------------
# propose_webui_configure — bad parameters
# ---------------------------------------------------------------------------


def test_propose_webui_configure_missing_intent():
    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "   ", "webui_path": "/webui/#/routing/ospf"},
    )
    assert result["error"] == "bad_parameters"


def test_propose_webui_configure_missing_webui_path():
    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure OSPF", "webui_path": ""},
    )
    assert result["error"] == "bad_parameters"


# ---------------------------------------------------------------------------
# webui_configure — requires approval
# ---------------------------------------------------------------------------


def test_webui_configure_requires_approval():
    """Calling webui_configure without an APPROVED action_id → not_approved."""
    result = tr.execute_tool("webui_configure", {"action_id": "act_nonexistent"})
    assert result["error"] == "not_approved"


# ---------------------------------------------------------------------------
# webui_configure — iterates plan steps (happy path)
# ---------------------------------------------------------------------------


def test_webui_configure_iterates_plan_steps(monkeypatch):
    """Approve first, mock webui_act_by_intent to succeed; assert mark_executed
    called once when verify_text becomes present."""
    plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add Process"}, "value": None},
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Process ID"},
            "value": "100",
        },
    ]
    # Register + approve an action with the plan stored in params. Use a
    # verify_text so the loop has a clean termination signal — otherwise
    # the new "always re-plan when verify_text is null" behavior would
    # keep iterating until cap.
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "configure OSPF",
            "webui_path": "/webui/#/routing/ospf",
            "plan": plan,
            "verify_text": "OSPF 100",
            "session_id": "sess_test",
        },
    )
    approve_action(action_id)

    act_calls: list[dict] = []

    def _fake_act_by_intent(**kwargs):
        act_calls.append(kwargs)
        return {"ok": True, "chosen_eid": "eid_1"}

    executed_ids: list[str] = []

    def _fake_mark_executed(aid: str) -> dict:
        executed_ids.append(aid)
        return {}

    monkeypatch.setattr(tr, "webui_act_by_intent", _fake_act_by_intent)
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": True})
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    # Patch mark_executed inside the function's local import scope
    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", _fake_mark_executed)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result.get("ok") is True
    assert len(act_calls) == 2  # one per step
    assert len(executed_ids) == 1
    assert executed_ids[0] == action_id


def test_webui_configure_null_verify_continues_re_planning(monkeypatch):
    """When propose-time plan is incomplete (e.g. only [click Add] for a
    static-route form) and verify_text is None, the loop must NOT bail
    after the first clean batch — it must re-describe and re-plan so
    the inner LLM can fill the form that appeared post-click. This is
    THE regression that caused single-step exits on multi-page forms.
    Loop should keep going until inner LLM returns empty plan."""
    propose_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    fill_plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "Prefix"}, "value": "10.0.0.0"},
        {"action": "click", "intent": {"role": "button", "name": "Apply"}, "value": None},
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "add static route",
            "webui_path": "/webui/#/staticRouting",
            "plan": propose_plan,
            "verify_text": None,  # propose-time planner couldn't predict
            "session_id": "sess_recover",
            "evidence": [],
        },
    )
    approve_action(action_id)

    act_count = 0

    def _act(**kwargs):
        nonlocal act_count
        act_count += 1
        return {"ok": True, "chosen_eid": f"eid_{act_count}"}

    draft_count = 0

    def _draft(*args, **kwargs):
        nonlocal draft_count
        draft_count += 1
        if draft_count == 1:
            # After click Add, surface the fill plan
            return {"plan": fill_plan, "verify_text": None, "risk": "fill form"}
        # After fill+Apply, inner LLM signals done with empty plan
        return {"plan": [], "verify_text": None, "risk": "done"}

    executed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", _act)
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})
    monkeypatch.setattr(confs, "mark_failed", lambda aid: None)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    # 1 (click Add) + 2 (fill Prefix + click Apply) = 3 act calls
    assert act_count == 3, f"expected 3 acts after re-plan, got {act_count}"
    # 2 draft calls: one to get fill_plan, one to get empty plan (done)
    assert draft_count == 2, f"expected 2 in-loop drafts, got {draft_count}"
    # An empty plan with null verify_text means "no more work" — the loop
    # surfaces that as inner_plan_empty (defensive — caller can decide).
    # Test passes either as ok=True (terminal) or as inner_plan_empty
    # error; the load-bearing assertion is that the loop kept going.
    assert result.get("ok") is True or result.get("error") == "inner_plan_empty"


# ---------------------------------------------------------------------------
# webui_configure — multi-propose chain: step failure feeds back to inner LLM
# ---------------------------------------------------------------------------


def test_webui_configure_step_fail_recovers_via_re_plan(monkeypatch):
    """Step fails in iter 1; draft_plan is invoked with previous_steps
    containing the failure; iter 2 succeeds; verify present → mark_executed."""
    initial_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    recovery_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add Route"}, "value": None},
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "add static route",
            "webui_path": "/webui/#/staticRouting",
            "plan": initial_plan,
            "verify_text": "Route added",
            "session_id": "sess_recover",
            "evidence": [],
        },
    )
    approve_action(action_id)

    act_calls: list[dict] = []
    call_count = 0

    def _act(**kwargs):
        nonlocal call_count
        call_count += 1
        act_calls.append(kwargs)
        if call_count == 1:
            return {"error": "element_not_found", "ok": False}
        return {"ok": True, "chosen_eid": "eid_recovered"}

    # Snapshot previous_steps at call time. _webui_configure passes the same
    # list by reference and keeps appending; capturing a deep copy preserves
    # what Haiku actually saw at this iteration.
    import copy

    draft_calls: list[dict] = []

    def _draft(*args, **kwargs):
        snapshot = copy.deepcopy(kwargs)
        draft_calls.append(snapshot)
        return {"plan": recovery_plan, "verify_text": "Route added", "risk": "ok"}

    verify_calls: list[dict] = []

    def _verify(**kwargs):
        verify_calls.append(kwargs)
        # Only present after the recovery batch
        return {"present": call_count >= 2}

    executed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", _act)
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", _verify)
    monkeypatch.setattr(tr, "draft_plan", _draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result.get("ok") is True
    assert result["iterations"] == 2
    assert len(act_calls) == 2  # one failed, one recovered
    assert len(draft_calls) == 1
    # previous_steps must reach the planner so it can adapt
    assert "previous_steps" in draft_calls[0]
    assert len(draft_calls[0]["previous_steps"]) == 1
    assert draft_calls[0]["previous_steps"][0]["status"] == "failed"
    assert len(executed_ids) == 1


def test_webui_configure_iteration_cap_hit(monkeypatch):
    """draft_plan always returns a non-empty NEW plan; verify always False.
    Loop must bail with iteration_cap_hit after _WEBUI_CONFIGURE_MAX_ITER
    execute-batches and call mark_failed exactly once.

    Perf/determinism: this is the only test that intentionally drives the
    loop all the way to the cap, so its runtime scales with the cap value.
    Two things are pinned so it stays fast and cap-value-independent:

      1. ``_WEBUI_CONFIGURE_MAX_ITER`` is monkeypatched to a small value (3).
         Every assertion below references ``tr._WEBUI_CONFIGURE_MAX_ITER``
         dynamically, so the expected iteration / draft counts track the
         patched value automatically — the test asserts the cap behavior
         without being coupled to whatever the production cap happens to be.
      2. The per-iteration adversarial vision check is disabled by forcing
         ``plan_vision_enabled=False``. With the real default (True), every
         loop iteration past the first enters the vision block, globs
         ``artifacts/screenshots/*<session_id>*`` and — if a prior live smoke
         left a matching screenshot dir on disk — base64-encodes the PNG and
         makes a REAL Anthropic vision API call. That single un-mocked call is
         what blew the cap=10 run out to ~100s on a machine with real
         artifacts; disabling it keeps the loop pure-in-memory.
    """
    # Pin the cap small so the loop is short regardless of the production value.
    monkeypatch.setattr(tr, "_WEBUI_CONFIGURE_MAX_ITER", 3)

    # Kill the real per-iteration vision path: with plan_vision_enabled=False
    # the loop never globs screenshots or calls check_plan_via_vision, so no
    # real LLM/vision request can fire no matter what's on disk.
    from backend.core.settings import Settings

    fake_settings = Settings.model_construct(plan_vision_enabled=False)
    monkeypatch.setattr("backend.core.settings.get_settings", lambda: fake_settings)

    initial_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Step 1"}, "value": None},
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "configure thing",
            "webui_path": "/webui/#/x",
            "plan": initial_plan,
            "verify_text": "Done",
            "session_id": "sess_cap",
            "evidence": [],
        },
    )
    approve_action(action_id)

    # Each draft returns a DIFFERENT plan so the stuck-detector doesn't fire
    # before the iteration cap.
    draft_count = 0

    def _draft(*args, **kwargs):
        nonlocal draft_count
        draft_count += 1
        return {
            "plan": [
                {
                    "action": "click",
                    "intent": {"role": "button", "name": f"Step {draft_count + 1}"},
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

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result["error"] == "iteration_cap_hit"
    assert result["iterations"] == tr._WEBUI_CONFIGURE_MAX_ITER
    # 1st batch came from propose; 3 in-loop drafts before the cap fires
    # (after iteration N=_MAX_ITER finishes verifying).
    assert draft_count == tr._WEBUI_CONFIGURE_MAX_ITER - 1
    assert len(failed_ids) == 1


def test_webui_configure_inner_plan_empty_mid_loop(monkeypatch):
    """First batch acts ok, verify miss, inner LLM returns empty plan with
    risk note → mark_failed with inner_plan_empty."""
    initial_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "do thing",
            "webui_path": "/webui/#/x",
            "plan": initial_plan,
            "verify_text": "Done",
            "session_id": "sess_empty",
            "evidence": [],
        },
    )
    approve_action(action_id)

    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {"plan": [], "verify_text": None, "risk": "target gone"},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result["error"] == "inner_plan_empty"
    assert result["risk"] == "target gone"
    assert len(failed_ids) == 1


def test_webui_configure_inner_plan_stuck(monkeypatch):
    """Inner LLM returns identical plan twice (same hash) → mark_failed
    with inner_plan_stuck."""
    initial_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    # Same plan as initial → hash matches → stuck detector fires on first
    # in-loop draft.
    repeated = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "do thing",
            "webui_path": "/webui/#/x",
            "plan": initial_plan,
            "verify_text": "Done",
            "session_id": "sess_stuck",
            "evidence": [],
        },
    )
    approve_action(action_id)

    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_act_by_intent", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"view": {"elements": []}, "session_id": "x"}
    )
    monkeypatch.setattr(tr, "webui_verify", lambda **kw: {"present": False})
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {"plan": repeated, "verify_text": "Done", "risk": "ok"},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result["error"] == "inner_plan_stuck"
    assert result["repeated_plan"] == repeated
    assert len(failed_ids) == 1


# ---------------------------------------------------------------------------
# Change 3 — session cleanup on propose error paths
# ---------------------------------------------------------------------------


def test_propose_webui_configure_closes_session_on_draft_failed(monkeypatch):
    """draft_plan raises → close_all_sessions called before handler returns."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(
        tr, "webui_open", lambda **kw: {"session_id": "sess_draft_fail", "view": {}}
    )
    monkeypatch.setattr(
        tr,
        "webui_describe_page",
        lambda **kw: {"session_id": "sess_draft_fail", "view": {"elements": []}},
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("inner LLM returned non-JSON: ...")),
    )

    close_calls: list[int] = []
    monkeypatch.setattr(tr, "close_all_sessions", lambda: close_calls.append(1))

    result = tr.execute_tool(
        "propose_webui_configure",
        {
            "intent": "add static route 10.0.0.0/24 via 192.168.1.1",
            "webui_path": "/webui/#/staticRouting",
        },
    )

    assert result["error"] == "draft_failed"
    assert len(close_calls) == 1, "close_all_sessions must be called exactly once on draft failure"


# ---------------------------------------------------------------------------
# propose_cli_configure / cli_configure — Chunk B (CLI AI configure)
# ---------------------------------------------------------------------------


def test_cli_configure_tools_present_in_schemas():
    """Both tools must appear in TOOL_SCHEMAS so the outer Haiku can call them."""
    schema_names = {s["name"] for s in tr.TOOL_SCHEMAS}
    assert "propose_cli_configure" in schema_names
    assert "cli_configure" in schema_names


def test_cli_configure_in_write_tools():
    """cli_configure is a write — must require approval gate."""
    assert "cli_configure" in tr.WRITE_TOOLS


def test_propose_cli_configure_happy_path(monkeypatch):
    """Mock the chain (search_docs, show_running_config, draft_cli_plan);
    assert action_id returned with full preview."""
    rag_result = {"results": [{"text": "OSPF reference", "source": "ospf.pdf", "section": "OSPF"}]}
    drafted = {
        "config_commands": ["router ospf 100", "network 10.0.0.0 0.255.255.255 area 0", "exit"],
        "verify_command": "show ip ospf | include 100",
        "verify_pattern": r'Routing Process "ospf 100"',
        "risk": "Adds OSPF process 100.",
    }
    monkeypatch.setattr(tr, "_search_docs", lambda **kw: rag_result)
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "hostname LAB\n!\nend")
    monkeypatch.setattr(tr, "draft_cli_plan", lambda *a, **kw: drafted)

    result = tr.execute_tool(
        "propose_cli_configure",
        {"intent": "Configure OSPF process 100 area 0"},
    )

    assert result["status"] == "awaiting_approval"
    assert result["action_id"].startswith("act_")
    assert result["execute_tool"] == "cli_configure"
    assert result["preview"]["command_count"] == 3
    assert result["preview"]["config_commands"][0] == "router ospf 100"
    assert "ospf 100" in result["preview"]["verify_pattern"]


def test_propose_cli_configure_missing_intent():
    result = tr.execute_tool("propose_cli_configure", {"intent": "  "})
    assert result["error"] == "bad_parameters"


def test_propose_cli_configure_inner_refusal_returns_intent_not_mappable(monkeypatch):
    """Inner LLM returns empty config_commands → intent_not_mappable."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "")
    monkeypatch.setattr(
        tr,
        "draft_cli_plan",
        lambda *a, **kw: {
            "config_commands": [],
            "verify_command": "",
            "verify_pattern": "",
            "risk": "Intent not a CLI configuration task — this is a read query.",
        },
    )

    result = tr.execute_tool("propose_cli_configure", {"intent": "what's the uptime"})
    assert result["error"] == "intent_not_mappable"
    assert "read query" in result["message"]


def test_propose_cli_configure_denylist_rejects_reload(monkeypatch):
    """Inner LLM returns a plan containing 'reload' → unsafe_command before
    propose_action ever runs. The human never sees the dangerous preview."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "")
    monkeypatch.setattr(
        tr,
        "draft_cli_plan",
        lambda *a, **kw: {
            "config_commands": ["hostname FOO", "reload"],
            "verify_command": "show running-config | include hostname",
            "verify_pattern": "hostname FOO",
            "risk": "renames + reboots",
        },
    )

    result = tr.execute_tool("propose_cli_configure", {"intent": "rename to FOO and reboot"})
    assert result["error"] == "unsafe_command"
    assert "reload" in result["message"].lower()
    assert result["drafted_commands"] == ["hostname FOO", "reload"]


def test_propose_cli_configure_denylist_rejects_bad_verify_command(monkeypatch):
    """verify_command that doesn't start with 'show ' → unsafe_command."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "")
    monkeypatch.setattr(
        tr,
        "draft_cli_plan",
        lambda *a, **kw: {
            "config_commands": ["hostname FOO"],
            "verify_command": "reload",  # not a show command
            "verify_pattern": "FOO",
            "risk": "x",
        },
    )

    result = tr.execute_tool("propose_cli_configure", {"intent": "rename to FOO"})
    assert result["error"] == "unsafe_command"
    assert "show" in result["message"].lower()


def test_propose_cli_configure_draft_failed(monkeypatch):
    """Inner LLM raises RuntimeError → draft_failed error."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "")

    def _boom(*a, **kw):
        raise RuntimeError("inner LLM returned non-JSON: blabla")

    monkeypatch.setattr(tr, "draft_cli_plan", _boom)

    result = tr.execute_tool("propose_cli_configure", {"intent": "something"})
    assert result["error"] == "draft_failed"
    assert "non-JSON" in result["message"]


def test_cli_configure_dispatcher_pulls_params_from_action(monkeypatch):
    """The _cli_configure wrapper must read config_commands/verify_command/
    verify_pattern from the stored action dict and call write_tools.cli_configure
    with the narrow signature."""
    action_id = propose_action(
        "cli_configure",
        {
            "intent": "configure ospf",
            "config_commands": ["router ospf 100", "exit"],
            "verify_command": "show ip ospf",
            "verify_pattern": "ospf 100",
            "risk": "low",
            "evidence": [],
        },
    )
    approve_action(action_id)

    captured: dict[str, object] = {}

    def _fake_cli_configure(**kwargs):
        captured.update(kwargs)
        return {"tool": "cli_configure", "ok": True}

    monkeypatch.setattr(tr.write_tools, "cli_configure", _fake_cli_configure)

    result = tr.execute_tool("cli_configure", {"action_id": action_id})

    assert result["tool"] == "cli_configure"
    assert captured["action_id"] == action_id
    assert captured["config_commands"] == ["router ospf 100", "exit"]
    assert captured["verify_command"] == "show ip ospf"
    assert captured["verify_pattern"] == "ospf 100"


def test_cli_configure_dispatcher_requires_approval():
    """Layer-1 dispatcher refusal — no APPROVED action_id → not_approved."""
    result = tr.execute_tool("cli_configure", {"action_id": "act_nonexistent"})
    assert result["error"] == "not_approved"


def test_propose_webui_configure_closes_session_on_intent_not_mappable(monkeypatch):
    """Empty plan (intent_not_mappable) → close_all_sessions called before handler returns."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_no_map", "view": {}})
    monkeypatch.setattr(
        tr,
        "webui_describe_page",
        lambda **kw: {"session_id": "sess_no_map", "view": {"elements": []}},
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: {
            "plan": [],
            "verify_text": None,
            "risk": "Page mismatch — target not visible",
        },
    )

    close_calls: list[int] = []
    monkeypatch.setattr(tr, "close_all_sessions", lambda: close_calls.append(1))

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure OSPF", "webui_path": "/webui/#/routing/ospf"},
    )

    assert result["error"] == "intent_not_mappable"
    assert len(close_calls) == 1, (
        "close_all_sessions must be called exactly once on intent_not_mappable"
    )


# ---------------------------------------------------------------------------
# Chunk 7 — conflict_detector wired into propose_cli_configure +
#            propose_webui_configure
# ---------------------------------------------------------------------------


def test_propose_cli_configure_attaches_conflict_fields_on_match(monkeypatch):
    """When draft_cli_plan returns commands whose anchor matches running-config,
    conflict fields appear in both the returned preview sub-dict and the
    stored action params."""
    from backend.orchestration.confirmations import get_action

    rag_result = {"results": [{"text": "OSPF ref", "source": "ospf.pdf", "section": "OSPF"}]}
    drafted = {
        "config_commands": ["router ospf 1", " network 10.0.0.0 0.0.0.255 area 0"],
        "verify_command": 'show ip ospf | include "ospf 1"',
        "verify_pattern": "ospf 1",
        "risk": "Adds OSPF process 1.",
    }
    running_cfg = "!\nrouter ospf 1\n network 10.0.0.0 0.0.0.255 area 0\n!\n"

    monkeypatch.setattr(tr, "_search_docs", lambda **kw: rag_result)
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)
    monkeypatch.setattr(tr, "draft_cli_plan", lambda *a, **kw: drafted)

    result = tr.execute_tool(
        "propose_cli_configure",
        {"intent": "Configure OSPF process 1 area 0"},
    )

    assert result["status"] == "awaiting_approval"
    # Conflict fields in top-level preview_meta, NOT inside preview sub-dict
    assert result["preview_meta"]["existing_entity"] == "router ospf 1"
    assert "is_exact_match" in result["preview_meta"]
    assert "existing_entity" not in result["preview"]  # preview stays scoped

    action = get_action(result["action_id"])
    assert action["preview_meta"]["existing_entity"] == "router ospf 1"
    assert "is_exact_match" in action["preview_meta"]
    # Regression guard: params stays clean for executor splat
    assert "existing_entity" not in action["params"]
    assert "is_exact_match" not in action["params"]

    # Sanity: draft_plan was NOT involved (cli path uses draft_cli_plan)
    # — just confirm running_config was passed to the tool
    assert result["action_id"].startswith("act_")


def test_propose_webui_configure_attaches_conflict_when_equivalent_cli_matches(monkeypatch):
    """draft_plan returns equivalent_cli_commands matching running-config stanza;
    conflict fields appear in preview sub-dict and stored params.
    Also asserts draft_plan was called with running_config kwarg populated."""

    from backend.orchestration.confirmations import get_action

    rag_result = {"results": [{"text": "VLAN ref", "source": "vlan.pdf", "section": "VLAN"}]}
    open_result = {"session_id": "sess_v30", "view": {}}
    desc_result = {"session_id": "sess_v30", "view": {"elements": []}}
    running_cfg = "!\nvlan 30\n name OFFICE\n!\n"

    drafted = {
        "plan": [{"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None}],
        "verify_text": "30",
        "risk": "Creates VLAN 30.",
        "equivalent_cli_commands": ["vlan 30", " name OFFICE"],
    }

    draft_plan_calls: list[dict] = []

    def _draft_plan(*args, **kwargs):
        draft_plan_calls.append(kwargs)
        return drafted

    monkeypatch.setattr(tr, "_search_docs", lambda **kw: rag_result)
    monkeypatch.setattr(tr, "webui_open", lambda **kw: open_result)
    monkeypatch.setattr(tr, "webui_describe_page", lambda **kw: desc_result)
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)
    monkeypatch.setattr(tr, "draft_plan", _draft_plan)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add VLAN 30 named OFFICE", "webui_path": "/webui/#/vlan"},
    )

    assert result["status"] == "awaiting_approval"
    # Conflict fields in top-level preview_meta, NOT inside preview sub-dict
    assert result["preview_meta"]["existing_entity"] == "vlan 30"
    assert "is_exact_match" in result["preview_meta"]
    assert "existing_entity" not in result["preview"]  # preview stays scoped

    action = get_action(result["action_id"])
    assert action["preview_meta"]["existing_entity"] == "vlan 30"
    # Regression guard: params stays clean for executor splat
    assert "existing_entity" not in action["params"]
    assert "is_exact_match" not in action["params"]

    # The structural heuristic replaced the old detector draft_plan call.
    # desc_result has empty elements → no trigger button found → form-open skipped.
    # draft_plan is called ONCE (the authoritative call only).
    assert len(draft_plan_calls) == 1
    # Authoritative draft must have received running_config=
    assert draft_plan_calls[-1].get("running_config") == running_cfg


def test_propose_webui_configure_skips_detector_when_equivalent_cli_empty(monkeypatch):
    """When draft_plan returns equivalent_cli_commands=[], the conflict
    detector is skipped — preview_meta is None in the returned result,
    and no exception is raised."""

    rag_result = {"results": [{"text": "x", "source": "s", "section": "S"}]}
    open_result = {"session_id": "sess_empty_cli", "view": {}}
    desc_result = {"session_id": "sess_empty_cli", "view": {"elements": []}}
    running_cfg = "!\nvlan 30\n name OFFICE\n!\n"

    drafted = {
        "plan": [{"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None}],
        "verify_text": "30",
        "risk": "Adds VLAN.",
        "equivalent_cli_commands": [],
    }

    draft_plan_calls: list[dict] = []

    def _draft_plan(*args, **kwargs):
        draft_plan_calls.append(kwargs)
        return drafted

    monkeypatch.setattr(tr, "_search_docs", lambda **kw: rag_result)
    monkeypatch.setattr(tr, "webui_open", lambda **kw: open_result)
    monkeypatch.setattr(tr, "webui_describe_page", lambda **kw: desc_result)
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)
    monkeypatch.setattr(tr, "draft_plan", _draft_plan)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add VLAN 30", "webui_path": "/webui/#/vlan"},
    )

    assert result["status"] == "awaiting_approval"
    assert result["preview_meta"] is None

    # The structural heuristic replaced the old detector draft_plan call.
    # desc_result has empty elements → no trigger button found → form-open skipped.
    # draft_plan is called ONCE (the authoritative call only).
    assert len(draft_plan_calls) == 1
    assert draft_plan_calls[-1].get("running_config") == running_cfg


# ---------------------------------------------------------------------------
# Regression tests — Phase C bugfix (preview_meta separation)
# ---------------------------------------------------------------------------


def test_set_hostname_execute_params_contain_no_propose_metadata(monkeypatch):
    """Regression: chunk 7 leaked propose-time conflict fields into action.params,
    which broke set_hostname() splat in the execute path with TypeError.
    Confirm params is clean even when a conflict IS detected."""
    from backend.orchestration.confirmations import get_action

    # Exact match so conflict IS detected — exercises the regression path.
    running_cfg = "!\nhostname c1111-lab\n!\n"
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)

    result = tr._TOOL_FUNCS["propose_set_hostname"]("c1111-lab")

    # Conflict was detected → preview_meta carries it
    assert result["preview_meta"] is not None
    assert result["preview_meta"]["existing_entity"] == "hostname c1111-lab"

    # action.params stays CLEAN — only the executor's kwargs survive
    action = get_action(result["action_id"])
    assert action["params"] == {"new_name": "c1111-lab"}
    assert "existing_entity" not in action["params"]
    assert "existing_block" not in action["params"]
    assert "is_exact_match" not in action["params"]

    # And action.preview_meta carries the conflict for the UI
    assert action["preview_meta"]["existing_entity"] == "hostname c1111-lab"


# ---------------------------------------------------------------------------
# Chunk 10 — OverloadedError wrapping in propose tools
# ---------------------------------------------------------------------------


def _make_overloaded_error(request_id: str = "req_test_registry_529") -> AnthropicOverloadedError:
    """Build a real OverloadedError with a real httpx.Response matching the
    SDK's APIStatusError.__init__ signature."""
    mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    mock_response = httpx.Response(
        status_code=529,
        headers={"request-id": request_id},
        request=mock_request,
    )
    return AnthropicOverloadedError(
        message="Overloaded",
        response=mock_response,
        body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )


def test_propose_cli_configure_wraps_overloaded_error(monkeypatch):
    """When draft_cli_plan raises OverloadedError, _propose_cli_configure must
    return the structured llm_overloaded dict instead of propagating the exception."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "hostname LAB\n!\nend")

    err = _make_overloaded_error("req_cli_529")
    monkeypatch.setattr(tr, "draft_cli_plan", lambda *a, **kw: (_ for _ in ()).throw(err))

    result = tr.execute_tool("propose_cli_configure", {"intent": "add VLAN 30"})

    assert result["error"] == "llm_overloaded"
    assert "overloaded" in result["message"].lower()
    assert result["request_id"] == "req_cli_529"


def test_propose_webui_configure_wraps_overloaded_error(monkeypatch):
    """When draft_plan raises OverloadedError, _propose_webui_configure must
    return the structured llm_overloaded dict and close the orphaned session."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": "x", "source": "s", "section": "S"}]},
    )
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_overload", "view": {}})
    monkeypatch.setattr(
        tr,
        "webui_describe_page",
        lambda **kw: {"session_id": "sess_overload", "view": {"elements": []}},
    )

    err = _make_overloaded_error("req_webui_529")
    monkeypatch.setattr(tr, "draft_plan", lambda *a, **kw: (_ for _ in ()).throw(err))

    close_calls: list[int] = []
    monkeypatch.setattr(tr, "close_all_sessions", lambda: close_calls.append(1))

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure OSPF", "webui_path": "/webui/#/routing/ospf"},
    )

    assert result["error"] == "llm_overloaded"
    assert "overloaded" in result["message"].lower()
    assert result["request_id"] == "req_webui_529"
    assert len(close_calls) == 1, (
        "close_all_sessions must be called on overload to clean up session"
    )


# ---------------------------------------------------------------------------
# Open-form-for-planning probe (Fix: DHCP list-view hallucination)
# ---------------------------------------------------------------------------
#
# Scenario: the list page only has an "Add" button.  _propose_webui_configure
# must use the structural heuristic (no LLM call) to detect the list page and:
#   1. Detect: trigger-name button present AND no textboxes in view elements.
#   2. Call webui_open_form_for_planning (NOT webui_act_by_intent, which is
#      approval-gated) with action="click".
#   3. Re-describe the page to get the open-form view.
#   4. Call draft_plan ONCE (authoritative) against the open-form view.
#   5. Return the fill+submit plan — NOT the single [click Add] step.


def _stub_basics(monkeypatch, *, rag_text: str = "DHCP help") -> None:
    """Monkeypatch _search_docs, webui_open, show_running_config to standard stubs."""
    monkeypatch.setattr(
        tr,
        "_search_docs",
        lambda **kw: {"results": [{"text": rag_text, "source": "dhcp.pdf", "section": "DHCP"}]},
    )
    monkeypatch.setattr(
        tr,
        "webui_open",
        lambda **kw: {
            "session_id": "sess_dhcp",
            "view": {"elements": [{"role": "button", "name": "Add", "eid": "e_001"}]},
        },
    )
    # SSH not available in unit tests — stub the running-config read.
    import backend.cli_agent.read_tools as rt

    monkeypatch.setattr(rt, "show_running_config", lambda: "")


def test_propose_opens_form_and_drafts_against_real_fields(monkeypatch):
    """Primary happy path: single-click Add plan triggers form-open + re-draft."""
    # List-view describe (initial)
    list_view = {"view_id": "list_v", "elements": [{"role": "button", "name": "Add", "eid": "e_1"}]}
    # Open-form view (after clicking Add)
    form_view = {
        "view_id": "form_v",
        "elements": [
            {"role": "textbox", "name": "Pool Name", "eid": "e_10"},
            {"role": "textbox", "name": "Network", "eid": "e_11"},
            {"role": "combobox", "name": "Subnet Mask", "eid": "e_12", "options": ["/24", "/25"]},
            {"role": "textbox", "name": "Starting ip", "eid": "e_13"},
            {"role": "textbox", "name": "Ending ip", "eid": "e_14"},
            {"role": "button", "name": "Apply to Device", "eid": "e_15"},
        ],
    }

    fill_plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "Pool Name"}, "value": "CORP"},
        {"action": "fill", "intent": {"role": "textbox", "name": "Network"}, "value": "10.0.0.0"},
        {
            "action": "select",
            "intent": {"role": "combobox", "name": "Subnet Mask"},
            "value": "/24",
        },
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Starting ip"},
            "value": "10.0.0.100",
        },
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Ending ip"},
            "value": "10.0.0.200",
        },
        {
            "action": "click",
            "intent": {"role": "button", "name": "Apply to Device"},
            "value": None,
        },
    ]

    describe_calls: list[str] = []

    def _fake_describe(**kw):
        describe_calls.append(kw.get("session_id", "?"))
        # First call (initial describe after webui_open) returns list view.
        # Second call (after form-open click) returns form view.
        if len(describe_calls) == 1:
            return {"session_id": "sess_dhcp", "view": list_view}
        return {"session_id": "sess_dhcp", "view": form_view}

    draft_calls: list[dict] = []

    def _fake_draft(intent_arg, rag, view, **kw):
        draft_calls.append({"view": view})
        # With the structural heuristic there is only ONE draft_plan call
        # (the authoritative one).  The heuristic fires on the list view's
        # "Add" button, opens the form, re-describes → authoritative draft
        # sees form_view and returns the fill plan.
        return {"plan": fill_plan, "verify_text": "Pool CORP created", "risk": "low"}

    form_open_calls: list[dict] = []

    def _fake_open_form(session_id, intent):
        form_open_calls.append({"session_id": session_id, "intent": intent})
        return {"ok": True, "view": form_view, "session_id": session_id}

    _stub_basics(monkeypatch)
    monkeypatch.setattr(tr, "webui_describe_page", _fake_describe)
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    monkeypatch.setattr(tr, "webui_open_form_for_planning", _fake_open_form)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool CORP 10.0.0.0/24 100-200", "webui_path": "/webui/#/dhcp"},
    )

    assert result["status"] == "awaiting_approval", result
    # The proposed plan must be the FILL plan, not the single [click Add] step.
    plan = result["preview"]["plan"]
    actions_in_plan = [s["action"] for s in plan]
    assert "fill" in actions_in_plan, f"Expected fill steps in plan; got {actions_in_plan}"
    assert "click" in actions_in_plan  # Apply to Device click is fine
    # No "Add" click must appear in the final plan (it was done at propose time).
    add_clicks = [
        s for s in plan if s["action"] == "click" and s["intent"].get("name") == "Add"
    ]
    assert add_clicks == [], f"Add click must not appear in proposed plan; got {add_clicks}"

    # webui_open_form_for_planning was called exactly once with action="click".
    assert len(form_open_calls) == 1
    assert form_open_calls[0]["intent"]["action"] == "click"

    # Heuristic replaces the old detector draft_plan call.
    # draft_plan is now called ONCE only (the authoritative call).
    assert len(draft_calls) == 1
    # That single call must use the form view (not the list view).
    assert draft_calls[0]["view"]["view_id"] == "form_v"


def test_propose_skips_form_open_when_plan_already_has_fills(monkeypatch):
    """Backward compat: if describe already shows a form, no form-open should happen."""
    # A view that already has fill-able fields (form is open).
    form_view = {
        "view_id": "already_open",
        "elements": [
            {"role": "textbox", "name": "Hostname", "eid": "e_1"},
            {"role": "button", "name": "Apply", "eid": "e_2"},
        ],
    }
    fill_plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "Hostname"}, "value": "router1"},
        {"action": "click", "intent": {"role": "button", "name": "Apply"}, "value": None},
    ]

    form_open_calls: list = []
    draft_calls: list = []

    def _fake_draft(*a, **kw):
        draft_calls.append(1)
        # Every draft call returns the fill plan directly — form is visible.
        return {"plan": fill_plan, "verify_text": "Hostname changed", "risk": "low"}

    _stub_basics(monkeypatch)
    monkeypatch.setattr(
        tr,
        "webui_describe_page",
        lambda **kw: {"session_id": "sess_hn", "view": form_view},
    )
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    monkeypatch.setattr(
        tr,
        "webui_open_form_for_planning",
        lambda sid, intent: form_open_calls.append(1) or {"ok": True},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "change hostname to router1", "webui_path": "/webui/#/general"},
    )

    assert result["status"] == "awaiting_approval"
    # Form-open helper must NOT be called when a submit button ("Apply") is present
    # (heuristic condition (b) not satisfied → form already open → skip block entirely).
    assert form_open_calls == [], "webui_open_form_for_planning must not be called when form is already open"
    # Heuristic replaced the old detector draft_plan call.
    # draft_plan is called ONCE only (the authoritative call).
    assert len(draft_calls) == 1


def test_propose_form_open_failure_falls_back_gracefully(monkeypatch):
    """If webui_open_form_for_planning fails, propose continues with the list view."""
    list_view = {
        "view_id": "list_v2",
        "elements": [{"role": "button", "name": "Add", "eid": "e_1"}],
    }
    # Even from the list view the planner produces a usable (if suboptimal) plan.
    fallback_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
        {"action": "fill", "intent": {"role": "textbox", "name": "Name"}, "value": "X"},
    ]

    draft_count_fb = [0]

    def _fake_draft(*a, **kw):
        draft_count_fb[0] += 1
        # With the structural heuristic there is no preliminary draft_plan call.
        # The heuristic fires on the list view's "Add" button but the form-open
        # helper returns an error, so the authoritative draft (the only call)
        # receives the original list view and returns a usable fallback plan.
        return {"plan": fallback_plan, "verify_text": "Saved", "risk": "low"}

    _stub_basics(monkeypatch)
    monkeypatch.setattr(
        tr,
        "webui_describe_page",
        lambda **kw: {"session_id": "sess_fb", "view": list_view},
    )
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    # Form-open helper fails.
    monkeypatch.setattr(
        tr,
        "webui_open_form_for_planning",
        lambda sid, intent: {"error": "open_form_click_failed", "failure_reason": "unknown_eid"},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add thing", "webui_path": "/webui/#/x"},
    )

    # Must still reach awaiting_approval — graceful fallback.
    assert result["status"] == "awaiting_approval", result


def test_propose_form_open_helper_receives_click_action(monkeypatch):
    """webui_open_form_for_planning is only ever called with action='click', even
    if the preliminary plan's intent dict didn't originally include 'action'."""
    list_view = {
        "view_id": "lv",
        "elements": [{"role": "button", "name": "Add", "eid": "e_1"}],
    }
    form_view = {
        "view_id": "fv",
        "elements": [{"role": "textbox", "name": "Name", "eid": "e_2"}],
    }

    received_intents: list[dict] = []

    def _fake_open_form(session_id, intent):
        received_intents.append(dict(intent))
        return {"ok": True, "view": form_view, "session_id": session_id}

    describe_call = [0]

    def _fake_describe(**kw):
        describe_call[0] += 1
        return {"session_id": "s", "view": form_view if describe_call[0] > 1 else list_view}

    draft_call = [0]

    def _fake_draft(*a, **kw):
        draft_call[0] += 1
        # With the structural heuristic there is only ONE draft_plan call
        # (the authoritative call).  Heuristic opens the form; re-describe
        # returns form_view; authoritative draft sees the textbox and fills it.
        return {
            "plan": [{"action": "fill", "intent": {"role": "textbox", "name": "Name"}, "value": "X"}],
            "verify_text": "Saved",
            "risk": "low",
        }

    _stub_basics(monkeypatch)
    monkeypatch.setattr(tr, "webui_describe_page", _fake_describe)
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    monkeypatch.setattr(tr, "webui_open_form_for_planning", _fake_open_form)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    tr.execute_tool(
        "propose_webui_configure",
        {"intent": "create thing", "webui_path": "/webui/#/x"},
    )

    assert len(received_intents) == 1
    # Caller always enforces action="click" before passing to the helper.
    assert received_intents[0]["action"] == "click"


# ---------------------------------------------------------------------------
# Structural heuristic: LLM-free form-open detection
# ---------------------------------------------------------------------------


def test_heuristic_fires_for_list_page_with_add_button(monkeypatch):
    """Heuristic detects 'Add' button + no textboxes → opens form WITHOUT
    calling draft_plan first (eliminates the old detector draft call)."""
    list_view = {
        "view_id": "lv_heuristic",
        "elements": [{"role": "button", "name": "Add", "eid": "e_add"}],
    }
    form_view = {
        "view_id": "fv_heuristic",
        "elements": [
            {"role": "textbox", "name": "Pool Name", "eid": "e_pool"},
            {"role": "button", "name": "Apply to Device", "eid": "e_apply"},
        ],
    }

    fill_plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "Pool Name"}, "value": "MGMT"},
        {"action": "click", "intent": {"role": "button", "name": "Apply to Device"}, "value": None},
    ]

    describe_calls: list[str] = []

    def _fake_describe(**kw):
        describe_calls.append(kw.get("session_id", "?"))
        # First call returns list view; second (after form-open) returns form view.
        if len(describe_calls) == 1:
            return {"session_id": "sess_h", "view": list_view}
        return {"session_id": "sess_h", "view": form_view}

    form_open_calls: list[dict] = []

    def _fake_open_form(session_id, intent):
        form_open_calls.append({"session_id": session_id, "intent": intent})
        return {"ok": True, "view": form_view, "session_id": session_id}

    draft_calls: list[dict] = []

    def _fake_draft(intent_arg, rag, view, **kw):
        draft_calls.append({"view": view})
        return {"plan": fill_plan, "verify_text": "Pool MGMT created", "risk": "low"}

    _stub_basics(monkeypatch)
    monkeypatch.setattr(tr, "webui_describe_page", _fake_describe)
    monkeypatch.setattr(tr, "webui_open_form_for_planning", _fake_open_form)
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool MGMT 192.168.1.0/24", "webui_path": "/webui/#/dhcp"},
    )

    assert result["status"] == "awaiting_approval", result
    # Heuristic opened the form — final plan must be the fill plan.
    plan = result["preview"]["plan"]
    assert any(s["action"] == "fill" for s in plan), f"Expected fill steps; got {plan}"

    # Form-open was called exactly once by the heuristic.
    assert len(form_open_calls) == 1
    assert form_open_calls[0]["intent"]["action"] == "click"
    assert form_open_calls[0]["intent"]["name"] == "Add"

    # Key assertion: draft_plan called ONCE only (no preliminary/detector call).
    assert len(draft_calls) == 1, (
        f"Expected exactly 1 draft_plan call (heuristic replaces detector); got {len(draft_calls)}"
    )
    # That single call saw the form view, not the list view.
    assert draft_calls[0]["view"]["view_id"] == "fv_heuristic"


def test_heuristic_fires_for_list_page_with_search_textbox(monkeypatch):
    """Real-world DHCP list page: has a 'Search Menu Items' textbox AND an 'Add'
    button but NO submit button.  The old 'no textboxes' condition (b) was FALSE
    here and caused the heuristic to never fire, leading to hallucinated field
    names.  The new 'no submit button' condition (b) must correctly identify this
    as a list page and fire the form-open."""
    # The Cisco DHCP list page: grid filter + Add button, no submit.
    list_view_with_search = {
        "view_id": "dhcp_list_real",
        "elements": [
            # Search/filter textbox present (was breaking old heuristic)
            {"role": "textbox", "name": "Search Menu Items", "eid": "e_search"},
            {"role": "button", "name": "Add", "eid": "e_add"},
            # NO submit button — this is the list page, not an open form.
        ],
    }
    form_view = {
        "view_id": "dhcp_form_real",
        "elements": [
            {"role": "textbox", "name": "Pool Name", "eid": "e_pool"},
            {"role": "textbox", "name": "Network", "eid": "e_net"},
            {"role": "button", "name": "Apply to Device", "eid": "e_apply"},
        ],
    }
    fill_plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "Pool Name"}, "value": "CORP"},
        {"action": "fill", "intent": {"role": "textbox", "name": "Network"}, "value": "10.0.0.0"},
        {"action": "click", "intent": {"role": "button", "name": "Apply to Device"}, "value": None},
    ]

    describe_calls: list[int] = []

    def _fake_describe(**kw):
        describe_calls.append(1)
        if len(describe_calls) == 1:
            return {"session_id": "sess_dhcp_real", "view": list_view_with_search}
        return {"session_id": "sess_dhcp_real", "view": form_view}

    form_open_calls: list[dict] = []

    def _fake_open_form(session_id, intent):
        form_open_calls.append({"intent": intent})
        return {"ok": True, "view": form_view, "session_id": session_id}

    draft_calls: list[dict] = []

    def _fake_draft(intent_arg, rag, view, **kw):
        draft_calls.append({"view": view})
        return {"plan": fill_plan, "verify_text": "Pool CORP created", "risk": "low"}

    _stub_basics(monkeypatch)
    monkeypatch.setattr(tr, "webui_describe_page", _fake_describe)
    monkeypatch.setattr(tr, "webui_open_form_for_planning", _fake_open_form)
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool CORP 10.0.0.0/24", "webui_path": "/webui/#/dhcp"},
    )

    assert result["status"] == "awaiting_approval", result
    # KEY: search textbox present but no submit → heuristic MUST fire.
    assert len(form_open_calls) == 1, (
        "Heuristic must fire for list page that has a search textbox but no submit button"
    )
    assert form_open_calls[0]["intent"]["action"] == "click"
    assert form_open_calls[0]["intent"]["name"] == "Add"
    # draft_plan called once, against the opened form view.
    assert len(draft_calls) == 1
    assert draft_calls[0]["view"]["view_id"] == "dhcp_form_real"


def test_heuristic_skips_when_submit_button_present(monkeypatch):
    """When the view has a submit/apply button (form is already open), the
    heuristic must NOT fire — no form-open call, draft_plan called once."""
    form_view_open = {
        "view_id": "fv_open",
        "elements": [
            {"role": "textbox", "name": "IP Address", "eid": "e_ip"},
            # Add button present too — but submit button (b) disarms the heuristic.
            {"role": "button", "name": "Add", "eid": "e_add"},
            {"role": "button", "name": "Apply to Device", "eid": "e_apply"},
        ],
    }
    fill_plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "IP Address"}, "value": "10.0.0.1"},
        {"action": "click", "intent": {"role": "button", "name": "Apply to Device"}, "value": None},
    ]

    form_open_calls: list = []
    draft_calls: list[dict] = []

    def _fake_draft(intent_arg, rag, view, **kw):
        draft_calls.append({"view": view})
        return {"plan": fill_plan, "verify_text": "IP set", "risk": "low"}

    _stub_basics(monkeypatch)
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"session_id": "sess_fvo", "view": form_view_open}
    )
    monkeypatch.setattr(
        tr,
        "webui_open_form_for_planning",
        lambda sid, intent: form_open_calls.append(1) or {"ok": True},
    )
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "set IP to 10.0.0.1", "webui_path": "/webui/#/interface"},
    )

    assert result["status"] == "awaiting_approval"
    # Heuristic condition (b): submit button present → form already open → skip.
    assert form_open_calls == [], (
        "webui_open_form_for_planning must NOT be called when a submit button exists"
    )
    # Exactly one draft_plan call.
    assert len(draft_calls) == 1
    assert draft_calls[0]["view"]["view_id"] == "fv_open"


def test_heuristic_skips_when_no_trigger_button(monkeypatch):
    """When the initial view has no button in _FORM_TRIGGER_NAMES_LOWER, heuristic
    must not fire — form-open skipped, draft_plan called once."""
    no_trigger_view = {
        "view_id": "ntv",
        "elements": [
            {"role": "button", "name": "Save", "eid": "e_save"},
            {"role": "button", "name": "Cancel", "eid": "e_cancel"},
        ],
    }
    a_plan = [
        {"action": "click", "intent": {"role": "button", "name": "Save"}, "value": None}
    ]

    form_open_calls: list = []
    draft_calls: list = []

    _stub_basics(monkeypatch)
    monkeypatch.setattr(
        tr, "webui_describe_page", lambda **kw: {"session_id": "sess_ntv", "view": no_trigger_view}
    )
    monkeypatch.setattr(
        tr,
        "webui_open_form_for_planning",
        lambda sid, intent: form_open_calls.append(1) or {"ok": True},
    )
    monkeypatch.setattr(
        tr,
        "draft_plan",
        lambda *a, **kw: draft_calls.append(1) or {"plan": a_plan, "verify_text": "ok", "risk": "low"},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "save config", "webui_path": "/webui/#/save"},
    )

    assert result["status"] == "awaiting_approval"
    # No trigger button → heuristic does not fire.
    assert form_open_calls == [], "webui_open_form_for_planning must NOT be called without a trigger button"
    assert len(draft_calls) == 1


def test_heuristic_add_process_in_trigger_set(monkeypatch):
    """'Add Process' (OSPF) is in _FORM_TRIGGER_NAMES_LOWER — heuristic must fire."""
    ospf_list_view = {
        "view_id": "ospf_lv",
        "elements": [{"role": "button", "name": "Add Process", "eid": "e_ospf_add"}],
    }
    ospf_form_view = {
        "view_id": "ospf_fv",
        "elements": [
            {"role": "textbox", "name": "Process ID", "eid": "e_pid"},
            {"role": "button", "name": "OK", "eid": "e_ok"},
        ],
    }
    ospf_plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "Process ID"}, "value": "1"},
        {"action": "click", "intent": {"role": "button", "name": "OK"}, "value": None},
    ]

    describe_calls: list[int] = []

    def _fake_describe(**kw):
        describe_calls.append(1)
        if len(describe_calls) == 1:
            return {"session_id": "sess_ospf", "view": ospf_list_view}
        return {"session_id": "sess_ospf", "view": ospf_form_view}

    form_open_calls: list[dict] = []

    def _fake_open_form(session_id, intent):
        form_open_calls.append({"intent": intent})
        return {"ok": True, "view": ospf_form_view, "session_id": session_id}

    draft_calls: list[dict] = []

    def _fake_draft(intent_arg, rag, view, **kw):
        draft_calls.append({"view_id": view.get("view_id")})
        return {"plan": ospf_plan, "verify_text": "OSPF enabled", "risk": "low"}

    _stub_basics(monkeypatch)
    monkeypatch.setattr(tr, "webui_describe_page", _fake_describe)
    monkeypatch.setattr(tr, "webui_open_form_for_planning", _fake_open_form)
    monkeypatch.setattr(tr, "draft_plan", _fake_draft)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "enable OSPF process 1 area 0", "webui_path": "/webui/#/routing/ospf"},
    )

    assert result["status"] == "awaiting_approval", result
    # Heuristic must have fired on "Add Process".
    assert len(form_open_calls) == 1, "Heuristic must fire for 'Add Process'"
    assert form_open_calls[0]["intent"]["name"] == "Add Process"
    assert form_open_calls[0]["intent"]["action"] == "click"
    # Exactly one draft_plan call (no detector call).
    assert len(draft_calls) == 1
    assert draft_calls[0]["view_id"] == "ospf_fv"
