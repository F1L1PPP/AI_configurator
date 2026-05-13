"""Unit tests for orchestration.tool_registry — schemas + dispatcher."""

from __future__ import annotations

from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import (
    NotApproved,
)

# _clean_actions fixture is now in tests/conftest.py (autouse).


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_schemas_have_required_fields():
    for schema in tr.TOOL_SCHEMAS:
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"


def test_schema_names_match_dispatch_table():
    schema_names = {s["name"] for s in tr.TOOL_SCHEMAS}
    dispatch_names = set(tr.tool_names())
    assert schema_names == dispatch_names


def test_write_tools_require_action_id():
    for schema in tr.TOOL_SCHEMAS:
        if schema["name"] in ("set_hostname", "set_interface_ip"):
            assert "action_id" in schema["input_schema"]["required"]


def test_propose_tools_do_not_require_action_id():
    for schema in tr.TOOL_SCHEMAS:
        if schema["name"].startswith("propose_"):
            required = schema["input_schema"].get("required", [])
            assert "action_id" not in required


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_error_dict():
    result = tr.execute_tool("nonexistent_tool", {})
    assert result["error"].startswith("unknown tool")
    assert "available" in result


def test_dispatch_calls_correct_function(monkeypatch):
    # Patch the dispatch table entry directly — _TOOL_FUNCS caches the
    # original references at import time, so patching the source module
    # does nothing.
    monkeypatch.setitem(tr._TOOL_FUNCS, "show_version", lambda: {"v": "17.6"})
    result = tr.execute_tool("show_version", {})
    assert result == {"v": "17.6"}


def test_dispatch_wraps_non_dict_result(monkeypatch):
    monkeypatch.setitem(
        tr._TOOL_FUNCS,
        "show_running_config",
        lambda: "hostname c1111-lab",
    )
    result = tr.execute_tool("show_running_config", {})
    assert result == {"result": "hostname c1111-lab"}


def test_dispatch_wraps_list_result(monkeypatch):
    rows = [{"intf": "Gi0/0/0"}]
    monkeypatch.setitem(tr._TOOL_FUNCS, "show_ip_interface_brief", lambda: rows)
    result = tr.execute_tool("show_ip_interface_brief", {})
    assert result == {"result": rows}


def test_dispatcher_layer1_refuses_unknown_action_id(monkeypatch):
    """Layer 1 (dispatcher): unknown action_id → not_approved BEFORE the
    underlying function is ever called."""
    called = []

    def _spy(**kwargs):
        called.append(kwargs)
        return {}

    monkeypatch.setitem(tr._TOOL_FUNCS, "set_hostname", _spy)
    result = tr.execute_tool(
        "set_hostname",
        {"new_name": "R1", "action_id": "act_nonexistent"},
    )
    assert result["error"] == "not_approved"
    assert called == []  # function never invoked


def test_dispatcher_layer1_refuses_missing_action_id(monkeypatch):
    """Layer 1 (dispatcher): no action_id key at all → not_approved.
    set_hostname takes new_name + action_id; calling it directly without
    action_id would raise TypeError, but layer 1 catches it first."""
    called = []

    def _spy(**kwargs):
        called.append(kwargs)
        return {}

    monkeypatch.setitem(tr._TOOL_FUNCS, "set_hostname", _spy)
    result = tr.execute_tool("set_hostname", {"new_name": "R1"})
    assert result["error"] == "not_approved"
    assert called == []


def test_dispatcher_layer2_catches_not_approved_exception(monkeypatch):
    """Layer 2 (exception): even when layer 1 lets the call through,
    a NotApproved raised from the function (e.g. race after dispatcher
    check) still returns the same structured error."""
    from backend.orchestration.confirmations import approve_action, propose_action

    action_id = propose_action("set_hostname", {"name": "R1"})
    approve_action(action_id)

    def _raises(**kwargs):
        raise NotApproved("simulated race")

    monkeypatch.setitem(tr._TOOL_FUNCS, "set_hostname", _raises)
    result = tr.execute_tool(
        "set_hostname",
        {"new_name": "R1", "action_id": action_id},
    )
    assert result["error"] == "not_approved"


def test_dispatch_catches_bad_parameters(monkeypatch):
    """Bad params on a non-write tool path still surface as bad_parameters."""

    def _picky(required_arg: str) -> dict:
        return {"ok": required_arg}

    monkeypatch.setitem(tr._TOOL_FUNCS, "show_version", _picky)
    result = tr.execute_tool("show_version", {})
    assert result["error"] == "bad_parameters"


def test_dispatch_catches_unexpected_exception(monkeypatch):
    def _raises():
        raise RuntimeError("boom")

    monkeypatch.setitem(tr._TOOL_FUNCS, "show_version", _raises)
    result = tr.execute_tool("show_version", {})
    assert result["error"] == "tool_failed"


# ---------------------------------------------------------------------------
# propose_set_hostname creates an action_id
# ---------------------------------------------------------------------------


def test_propose_set_hostname_returns_awaiting_approval():
    result = tr.execute_tool("propose_set_hostname", {"new_name": "LAB-R1"})
    assert result["status"] == "awaiting_approval"
    assert result["action_id"].startswith("act_")
    assert "LAB-R1" in result["preview"]


def test_propose_set_interface_ip_returns_awaiting_approval():
    result = tr.execute_tool(
        "propose_set_interface_ip",
        {
            "interface": "Gi0/0/0",
            "ip": "10.0.0.1",
            "mask": "255.255.255.0",
        },
    )
    assert result["status"] == "awaiting_approval"
    assert "Gi0/0/0" in result["preview"]
