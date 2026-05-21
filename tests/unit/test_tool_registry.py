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
    # Routed port (or SSH-unreachable in unit tests) -> falls back to the
    # direct `set_interface_ip` propose; preview is the plain "Will set X"
    # string, not the cli_configure preview dict.
    assert isinstance(result["preview"], str)
    assert "Gi0/0/0" in result["preview"]
    assert result["execute_tool"] == "set_interface_ip"


# ---------------------------------------------------------------------------
# propose_set_interface_ip — L2-only port auto-redirect to SVI plan
# ---------------------------------------------------------------------------


def test_propose_set_interface_ip_redirects_to_svi_for_switchport(monkeypatch):
    """When the target port is a hardware switchport, the propose helper
    must NOT propose a raw `set_interface_ip` (which would silently fail
    at the L2/L3 boundary) — it must build a 3-block SVI plan and route
    it through cli_configure for the operator to approve once."""
    switchport_block = (
        "interface GigabitEthernet0/1/3\n switchport mode access\n switchport access vlan 1\nend\n"
    )
    monkeypatch.setattr(
        tr.read_tools,
        "show_running_config_interface",
        lambda iface: switchport_block,
    )

    result = tr.execute_tool(
        "propose_set_interface_ip",
        {
            "interface": "GigabitEthernet0/1/3",
            "ip": "192.168.40.1",
            "mask": "255.255.255.0",
        },
    )

    assert result["status"] == "awaiting_approval"
    assert result["execute_tool"] == "cli_configure"
    preview = result["preview"]
    assert isinstance(preview, dict)
    cmds = preview["config_commands"]
    # 3-block plan: vlan 40 + name, interface Vlan40 + ip + no shut,
    # interface Gi0/1/3 + switchport access vlan 40.
    assert "vlan 40" in cmds
    assert " name auto-vlan-40" in cmds
    assert "interface Vlan40" in cmds
    assert " ip address 192.168.40.1 255.255.255.0" in cmds
    assert "interface GigabitEthernet0/1/3" in cmds
    assert " switchport access vlan 40" in cmds
    # Verify-back must confirm the SVI got the IP (not just that VLAN exists).
    assert "Vlan40" in preview["verify_command"]
    assert "Vlan40" in preview["verify_pattern"]
    assert "192" in preview["verify_pattern"]
    # Risk text names the chassis-quirk reason so the operator sees WHY
    # we're swapping in a different plan.
    assert "switchport" in preview["risk"].lower()


def test_propose_set_interface_ip_keeps_direct_propose_for_routed_port(monkeypatch):
    """A truly routed port (no `switchport` in its running-config block)
    keeps the original fast-path `set_interface_ip` propose."""
    routed_block = (
        "interface GigabitEthernet0/0/0\n ip address 10.0.0.1 255.255.255.0\n no shutdown\nend\n"
    )
    monkeypatch.setattr(
        tr.read_tools,
        "show_running_config_interface",
        lambda iface: routed_block,
    )

    result = tr.execute_tool(
        "propose_set_interface_ip",
        {
            "interface": "GigabitEthernet0/0/0",
            "ip": "10.0.0.5",
            "mask": "255.255.255.0",
        },
    )

    assert result["status"] == "awaiting_approval"
    assert result["execute_tool"] == "set_interface_ip"
    assert isinstance(result["preview"], str)


def test_propose_set_interface_ip_falls_back_when_snapshot_read_fails(monkeypatch):
    """SSH read raising (router down, transient netmiko error) must NOT
    block the propose — fall back to the direct path. The chunk-1
    write-tool verify still catches the silent-failure case at execute
    time, so the loss of pre-check is degradation, not breakage."""

    def boom(iface):
        raise RuntimeError("ssh handshake failed")

    monkeypatch.setattr(tr.read_tools, "show_running_config_interface", boom)

    result = tr.execute_tool(
        "propose_set_interface_ip",
        {
            "interface": "GigabitEthernet0/1/3",
            "ip": "192.168.40.1",
            "mask": "255.255.255.0",
        },
    )

    assert result["status"] == "awaiting_approval"
    assert result["execute_tool"] == "set_interface_ip"


def test_derive_svi_vlan_id_uses_third_octet():
    assert tr._derive_svi_vlan_id("192.168.40.1") == 40
    assert tr._derive_svi_vlan_id("192.168.42.5") == 42
    assert tr._derive_svi_vlan_id("172.16.200.10") == 200


def test_derive_svi_vlan_id_falls_back_to_100_for_zero_or_default_vlan():
    """Third-octet 0 (10.0.0.x) or 1 (avoid VLAN 1 collision) -> 100."""
    assert tr._derive_svi_vlan_id("10.0.0.1") == 100
    assert tr._derive_svi_vlan_id("172.16.1.5") == 100


# ---------------------------------------------------------------------------
# Chunk 7 — conflict_detector wired into propose tools
# ---------------------------------------------------------------------------


def test_propose_set_hostname_exact_match_attaches_conflict_fields(monkeypatch):
    """When running-config contains an exact hostname match, the returned dict
    carries preview_meta and the stored action.preview_meta carries the conflict
    fields. action.params must NOT contain conflict fields (executor splat safety)."""
    from backend.orchestration.confirmations import get_action

    running_cfg = "!\nhostname c1111-lab\n!\n"
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)

    result = tr.execute_tool("propose_set_hostname", {"new_name": "c1111-lab"})

    assert result["status"] == "awaiting_approval"
    assert result["preview_meta"]["existing_entity"] == "hostname c1111-lab"
    assert result["preview_meta"]["is_exact_match"] is True

    action = get_action(result["action_id"])
    # Conflict fields in preview_meta, not params
    assert action["preview_meta"]["existing_entity"] == "hostname c1111-lab"
    assert action["preview_meta"]["is_exact_match"] is True
    # Regression guard: params stays clean for executor splat
    assert "existing_entity" not in action["params"]
    assert "existing_block" not in action["params"]
    assert "is_exact_match" not in action["params"]


def test_propose_set_hostname_different_no_conflict_fields(monkeypatch):
    """When the proposed hostname doesn't exist in running-config, the detector
    finds no match (find_existing_block searches for the PROPOSED anchor, not any
    existing hostname) — preview_meta is None, normal awaiting_approval shape."""
    running_cfg = "!\nhostname c1111-lab\n!\n"
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)

    result = tr.execute_tool("propose_set_hostname", {"new_name": "lab-new"})

    assert result["status"] == "awaiting_approval"
    # Anchor "hostname lab-new" does not appear in running-config → no conflict.
    assert result["preview_meta"] is None


def test_propose_set_hostname_ssh_read_failure_soft_falls(monkeypatch):
    """show_running_config raising must not block the propose — preview_meta is
    None, normal awaiting_approval shape preserved."""

    def _boom():
        raise Exception("ssh boom")

    monkeypatch.setattr(tr.read_tools, "show_running_config", _boom)

    result = tr.execute_tool("propose_set_hostname", {"new_name": "LAB-R1"})

    assert result["status"] == "awaiting_approval"
    assert result["preview_meta"] is None


# ---------------------------------------------------------------------------
# Signature guard — review fix #7
# ---------------------------------------------------------------------------


def test_execute_tool_rejects_unexpected_params(monkeypatch):
    """Stub accepting only (a, b) + unexpected extra key → bad_parameters."""

    def _stub(a: int, b: int) -> dict:
        return {"sum": a + b}

    monkeypatch.setitem(tr._TOOL_FUNCS, "show_version", _stub)
    result = tr.execute_tool("show_version", {"a": 1, "b": 2, "extra_field": "boom"})
    assert result["error"] == "bad_parameters"
    assert "extra_field" in result["message"]


def test_execute_tool_accepts_extras_for_kwargs_tools(monkeypatch):
    """Tool using **kwargs must NOT be rejected — extras are fine."""

    def _stub(**kwargs: object) -> dict:
        return {"received": list(kwargs.keys())}

    monkeypatch.setitem(tr._TOOL_FUNCS, "show_version", _stub)
    result = tr.execute_tool("show_version", {"a": 1, "unexpected": "ok"})
    assert "error" not in result or result.get("error") != "bad_parameters"
    assert "received" in result


# ---------------------------------------------------------------------------
# Existing conflict_detector tests (unchanged)
# ---------------------------------------------------------------------------


def test_propose_set_interface_ip_existing_attaches_conflict_fields(monkeypatch):
    """Non-SVI path: when running-config contains a matching interface stanza,
    conflict fields appear in preview_meta on both the returned dict and stored
    action. action.params must NOT contain conflict fields."""
    from backend.orchestration.confirmations import get_action

    routed_block = "interface Loopback0\n ip address 1.1.1.1 255.255.255.255\n no shutdown\n"
    running_cfg = f"!\n{routed_block}!\n"

    # show_running_config_interface must NOT return a switchport block
    # (otherwise the SVI redirect fires instead of the direct propose).
    monkeypatch.setattr(tr.read_tools, "show_running_config_interface", lambda iface: routed_block)
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: running_cfg)

    result = tr.execute_tool(
        "propose_set_interface_ip",
        {"interface": "Loopback0", "ip": "1.1.1.1", "mask": "255.255.255.255"},
    )

    assert result["status"] == "awaiting_approval"
    assert result["execute_tool"] == "set_interface_ip"
    assert result["preview_meta"]["existing_entity"] == "interface Loopback0"

    action = get_action(result["action_id"])
    assert action["preview_meta"]["existing_entity"] == "interface Loopback0"
    # Regression guard: params stays clean for executor splat
    assert "existing_entity" not in action["params"]
    assert "is_exact_match" not in action["params"]


# ---------------------------------------------------------------------------
# webui_configure describe_failed message propagation
# ---------------------------------------------------------------------------


def test_describe_failed_propagates_inner_message(monkeypatch):
    """describe_failed result must carry the inner message from webui_describe_page.

    Regression for act_20260521_921e52: describe_failed returned no message
    field (chat showed 'describe_failed: no message') because the wrap block
    in _webui_configure dropped the inner result's message field.
    """
    from backend.orchestration.confirmations import approve_action, propose_action

    # Build a minimal approved action so _webui_configure can get past
    # the HITL gate and into the loop.
    action_id = propose_action(
        "webui_configure",
        {
            "session_id": "sess_test",
            "intent": "configure DHCP",
            "plan": [
                {
                    "intent": {"role": "button", "name": "Apply"},
                    "action": "click",
                    "value": None,
                }
            ],
            "verify_text": None,
            "evidence": [],
        },
    )
    approve_action(action_id)

    # webui_act_by_intent returns success so the batch runs clean.
    monkeypatch.setattr(
        tr,
        "webui_act_by_intent",
        lambda session_id, intent, action_id: {"ok": True},
    )

    # webui_describe_page returns a describe error with an inner message.
    inner_error = {
        "error": "webui_describe_failed",
        "message": "session timed out",
        "exc_type": "TimeoutError",
        "session_id": "sess_test",
    }
    monkeypatch.setattr(tr, "webui_describe_page", lambda session_id: inner_error)

    # close_all_sessions is a no-op in unit tests.
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr._webui_configure(action_id=action_id)

    assert result["error"] == "describe_failed"
    assert result["message"] == "session timed out", (
        f"inner message was not propagated; got: {result.get('message')!r}"
    )
