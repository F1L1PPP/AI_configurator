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
    called once at the end."""
    plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add Process"}, "value": None},
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Process ID"},
            "value": "100",
        },
    ]
    # Register + approve an action with the plan stored in params
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "configure OSPF",
            "webui_path": "/webui/#/routing/ospf",
            "plan": plan,
            "verify_text": None,
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


# ---------------------------------------------------------------------------
# webui_configure — stops on step failure
# ---------------------------------------------------------------------------


def test_webui_configure_stops_on_step_failure(monkeypatch):
    """If step 2 (index 1) fails, mark_failed is called and error is returned."""
    plan = [
        {"action": "click", "intent": {"role": "button", "name": "Add"}, "value": None},
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Process ID"},
            "value": "100",
        },
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "configure OSPF",
            "webui_path": "/webui/#/routing/ospf",
            "plan": plan,
            "verify_text": None,
            "session_id": "sess_fail",
        },
    )
    approve_action(action_id)

    call_count = 0

    def _fail_on_second(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"ok": True, "chosen_eid": "eid_1"}
        return {"error": "element_not_found", "ok": False}

    failed_ids: list[str] = []

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid: failed_ids.append(aid) or {})
    monkeypatch.setattr(tr, "webui_act_by_intent", _fail_on_second)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result["error"] == "step_failed"
    assert result["step_index"] == 1
    assert len(failed_ids) == 1
    assert failed_ids[0] == action_id


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
