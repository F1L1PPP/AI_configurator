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


# ---------------------------------------------------------------------------
# CLI VLAN tool — propose_set_access_vlan / set_access_vlan
# ---------------------------------------------------------------------------


def test_propose_set_access_vlan_in_schemas() -> None:
    names = [t["name"] for t in tr.TOOL_SCHEMAS]
    assert "propose_set_access_vlan" in names
    assert "set_access_vlan" in names


def test_cli_vlan_tools_in_dispatch_table() -> None:
    assert "propose_set_access_vlan" in tr._TOOL_FUNCS
    assert "set_access_vlan" in tr._TOOL_FUNCS


def test_set_access_vlan_is_write_tool() -> None:
    assert "set_access_vlan" in tr.WRITE_TOOLS


def test_propose_set_access_vlan_returns_structured_dict() -> None:
    result = tr._TOOL_FUNCS["propose_set_access_vlan"](vlan_id=40, vlan_name="OFFICE")
    assert result["status"] == "awaiting_approval"
    assert result["execute_tool"] == "set_access_vlan"
    assert result["execute_params"]["vlan_id"] == 40
    assert result["execute_params"]["vlan_name"] == "OFFICE"


def test_dispatcher_refuses_cli_vlan_without_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_flow = MagicMock()
    monkeypatch.setitem(tr._TOOL_FUNCS, "set_access_vlan", mock_flow)

    aid = propose_action("set_access_vlan", {"vlan_id": 40, "vlan_name": "OFFICE"})
    result = tr.execute_tool(
        "set_access_vlan",
        {"vlan_id": 40, "vlan_name": "OFFICE", "action_id": aid},
    )
    assert result["error"] == "not_approved"
    mock_flow.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: propose next_step must point to inline buttons, not /preview
# ---------------------------------------------------------------------------


def test_next_step_no_longer_mentions_preview() -> None:
    """Agent used to tell users to 'open /preview and click APPROVE'. With
    inline buttons in chat that's confusing and wrong. All propose helpers
    must now point at the inline buttons."""
    cases = [
        ("propose_set_hostname", {"new_name": "LAB-X"}),
        (
            "propose_set_interface_ip",
            {"interface": "Gi0/0/0", "ip": "10.0.0.1", "mask": "255.255.255.0"},
        ),
        ("propose_set_access_vlan", {"vlan_id": 40, "vlan_name": "OFFICE"}),
        ("propose_webui_set_hostname", {"new_name": "LAB-X"}),
        ("propose_webui_add_access_vlan", {"vlan_id": 40, "vlan_name": "OFFICE"}),
    ]
    for name, kwargs in cases:
        res = tr._TOOL_FUNCS[name](**kwargs)
        ns = res["next_step"]
        assert "/preview" not in ns, f"{name}: next_step still mentions /preview: {ns!r}"
        assert "APPROVE" in ns.upper(), f"{name}: next_step should mention APPROVE button: {ns!r}"
        assert "EXECUTE" in ns.upper(), f"{name}: next_step should mention EXECUTE button: {ns!r}"


# ---------------------------------------------------------------------------
# Chunk 7 — conflict detection wired into propose_set_access_vlan
# ---------------------------------------------------------------------------


def test_propose_set_access_vlan_existing_attaches_conflict_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When running-config contains an existing vlan 30 stanza with a different
    name, conflict fields appear in preview_meta on the returned dict and stored
    action. action.params must NOT contain conflict fields (executor splat safety)."""
    from backend.orchestration.confirmations import get_action

    running_cfg = "!\nvlan 30\n name OLD\n!\n"

    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)

    result = tr._TOOL_FUNCS["propose_set_access_vlan"](vlan_id=30, vlan_name="RENAMED")

    assert result["status"] == "awaiting_approval"
    assert result["preview_meta"]["existing_entity"] == "vlan 30"
    assert result["preview_meta"]["is_exact_match"] is False

    action = get_action(result["action_id"])
    assert action["preview_meta"]["existing_entity"] == "vlan 30"
    assert action["preview_meta"]["is_exact_match"] is False
    # Regression guard: params stays clean for executor splat
    assert "existing_entity" not in action["params"]
    assert "is_exact_match" not in action["params"]


def test_propose_set_access_vlan_falls_back_to_vlan_brief_when_not_in_running_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1111-4P quirk: VLAN definitions live in vlan.dat and don't appear in
    `show running-config` as a `vlan N / name X` stanza, but `show vlan brief`
    is authoritative. When the detector returns None, fall back to vlan_brief.
    Exact-name match → is_exact_match=True (no-op write)."""
    # Running-config has VLAN 1 but NOT vlan 30
    running_cfg = "!\ninterface Vlan1\n ip address 192.168.10.1 255.255.255.0\n!\n"
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)
    monkeypatch.setattr(
        tr.read_tools,
        "show_vlan_brief",
        lambda: [
            {"vlan_id": "1", "vlan_name": "default", "status": "active"},
            {"vlan_id": "30", "vlan_name": "OFFICE", "status": "active"},
        ],
    )

    result = tr._TOOL_FUNCS["propose_set_access_vlan"](vlan_id=30, vlan_name="OFFICE")

    assert result["preview_meta"]["existing_entity"] == "vlan 30"
    assert result["preview_meta"]["existing_block"] == "vlan 30\n name OFFICE"
    assert result["preview_meta"]["is_exact_match"] is True


def test_propose_set_access_vlan_vlan_brief_fallback_rename_not_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback fires when running-config lacks the stanza, AND when the
    requested name differs from vlan_brief's name → is_exact_match=False
    (rename collision, not no-op)."""
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "!\nhostname X\n!\n")
    monkeypatch.setattr(
        tr.read_tools,
        "show_vlan_brief",
        lambda: [{"vlan_id": "30", "vlan_name": "OFFICE", "status": "active"}],
    )

    result = tr._TOOL_FUNCS["propose_set_access_vlan"](vlan_id=30, vlan_name="RENAMED")

    assert result["preview_meta"]["existing_entity"] == "vlan 30"
    assert result["preview_meta"]["is_exact_match"] is False


def test_propose_set_access_vlan_vlan_brief_fallback_skipped_when_detector_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the detector finds the VLAN in running-config (the normal IOS XE
    case), the vlan_brief fallback must NOT also fire — no double-detection."""
    running_cfg = "!\nvlan 30\n name OFFICE\n!\n"
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)

    # Sentinel: if show_vlan_brief gets called, fail the test loudly
    def boom() -> list[dict]:
        raise AssertionError("vlan_brief fallback fired despite detector match")

    monkeypatch.setattr(tr.read_tools, "show_vlan_brief", boom)

    result = tr._TOOL_FUNCS["propose_set_access_vlan"](vlan_id=30, vlan_name="OFFICE")

    assert result["preview_meta"]["existing_entity"] == "vlan 30"
    assert result["preview_meta"]["is_exact_match"] is True
