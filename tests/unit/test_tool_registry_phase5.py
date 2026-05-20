"""Unit tests for Phase 5 tool_registry additions:
- propose_webui_configure
- webui_configure
- Confirm low-level tools removed from TOOL_SCHEMAS
"""

from __future__ import annotations

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
    execute-batches and call mark_failed exactly once."""
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

    # draft_plan must have received running_config=
    assert len(draft_plan_calls) == 1
    assert draft_plan_calls[0].get("running_config") == running_cfg


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

    # draft_plan still received running_config= kwarg
    assert len(draft_plan_calls) == 1
    assert draft_plan_calls[0].get("running_config") == running_cfg


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
