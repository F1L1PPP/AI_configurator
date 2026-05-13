"""Tests that the VLAN add tools are registered + dispatched correctly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import backend.orchestration.tool_registry as tr
from backend.orchestration.confirmations import approve_action, propose_action


def test_propose_webui_add_access_vlan_in_schemas() -> None:
    names = [t["name"] for t in tr.TOOL_SCHEMAS]
    assert "propose_webui_add_access_vlan" in names
    schema = next(t for t in tr.TOOL_SCHEMAS if t["name"] == "propose_webui_add_access_vlan")
    assert schema["input_schema"]["required"] == ["vlan_id", "vlan_name"]
    assert schema["input_schema"]["properties"]["vlan_id"]["type"] == "integer"


def test_webui_add_access_vlan_in_schemas() -> None:
    names = [t["name"] for t in tr.TOOL_SCHEMAS]
    assert "webui_add_access_vlan" in names
    schema = next(t for t in tr.TOOL_SCHEMAS if t["name"] == "webui_add_access_vlan")
    assert set(schema["input_schema"]["required"]) == {"vlan_id", "vlan_name", "action_id"}


def test_vlan_tools_in_dispatch_table() -> None:
    assert "propose_webui_add_access_vlan" in tr._TOOL_FUNCS
    assert "webui_add_access_vlan" in tr._TOOL_FUNCS
    assert callable(tr._TOOL_FUNCS["propose_webui_add_access_vlan"])
    assert callable(tr._TOOL_FUNCS["webui_add_access_vlan"])


def test_webui_add_access_vlan_is_write_tool() -> None:
    """Must be in WRITE_TOOLS so the dispatcher's approval gate covers it
    AND the planner emits an `applied` event after success."""
    assert "webui_add_access_vlan" in tr.WRITE_TOOLS


def test_dispatcher_refuses_webui_add_access_vlan_without_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth layer 1: dispatcher refuses write tools whose
    action_id is missing or not APPROVED, before the flow runs."""
    # Stub the actual flow so this test stays decoupled from Playwright
    mock_flow = MagicMock()
    monkeypatch.setitem(tr._TOOL_FUNCS, "webui_add_access_vlan", mock_flow)

    # Propose but DON'T approve
    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    result = tr.execute_tool(
        "webui_add_access_vlan",
        {"vlan_id": 30, "vlan_name": "OFFICE", "action_id": action_id},
    )
    assert result["error"] == "not_approved"
    mock_flow.assert_not_called()


def test_dispatcher_calls_flow_when_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_flow = MagicMock(return_value={"tool": "webui_add_access_vlan", "vlan_id": 30})
    monkeypatch.setitem(tr._TOOL_FUNCS, "webui_add_access_vlan", mock_flow)

    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    approve_action(action_id)

    result = tr.execute_tool(
        "webui_add_access_vlan",
        {"vlan_id": 30, "vlan_name": "OFFICE", "action_id": action_id},
    )
    assert result == {"tool": "webui_add_access_vlan", "vlan_id": 30}
    mock_flow.assert_called_once_with(vlan_id=30, vlan_name="OFFICE", action_id=action_id)


def test_propose_returns_awaiting_approval_with_action_id() -> None:
    """The propose helper returns the structured dict the planner forwards to the user."""
    result = tr._TOOL_FUNCS["propose_webui_add_access_vlan"](vlan_id=30, vlan_name="OFFICE")
    assert result["status"] == "awaiting_approval"
    assert result["action_id"].startswith("act_")
    assert result["execute_tool"] == "webui_add_access_vlan"
    assert result["execute_params"]["vlan_id"] == 30
    assert result["execute_params"]["vlan_name"] == "OFFICE"
    assert result["execute_params"]["action_id"] == result["action_id"]
