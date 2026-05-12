"""Unit tests for orchestration.tool_registry — schemas + dispatcher."""

from __future__ import annotations

import pytest

from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import (
    NotApproved,
    _reset_for_testing,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_testing()
    yield
    _reset_for_testing()


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


def test_dispatch_catches_not_approved(monkeypatch):
    def _raises(**kwargs):
        raise NotApproved("nope")

    monkeypatch.setitem(tr._TOOL_FUNCS, "set_hostname", _raises)
    result = tr.execute_tool(
        "set_hostname",
        {"new_name": "R1", "action_id": "act_x"},
    )
    assert result["error"] == "not_approved"


def test_dispatch_catches_bad_parameters():
    # set_hostname requires new_name and action_id; passing nothing → TypeError
    result = tr.execute_tool("set_hostname", {})
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
    result = tr.execute_tool("propose_set_interface_ip", {
        "interface": "Gi0/0/0",
        "ip":        "10.0.0.1",
        "mask":      "255.255.255.0",
    })
    assert result["status"] == "awaiting_approval"
    assert "Gi0/0/0" in result["preview"]
