"""Unit tests for Chunk C4 — atlas-path propose/execute functions.

Tests are isolated: webui_* wrappers, draft_atlas_plan, and confirmations
helpers are all monkeypatched.  No real Playwright, no real SSH, no real LLM.

Mirrors the style of test_tool_registry_phase5.py.

Key regression lock: assert draft_atlas_plan / draft_plan is NOT called
during _webui_configure_atlas execution (no re-plan = inner_plan_empty fix).
"""

from __future__ import annotations

from backend.orchestration import tool_registry as tr
from backend.orchestration.confirmations import (
    approve_action,
    propose_action,
)

# _clean_actions autouse fixture is in tests/conftest.py.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RAG_RESULT = {
    "results": [
        {"text": "DHCP info", "source": "dhcp.pdf", "section": "DHCP Basics"},
    ]
}

_PERCEIVE_VIEW = {
    "route": "/webui/#/dhcp",
    "page_title": "DHCP",
    "fields": [
        {
            "key": "dhcp.pool_name",
            "label": "Pool Name",
            "role": "textbox",
            "widget": "input",
            "required": True,
            "value": "",
            "options": None,
        },
        {
            "key": "dhcp.network",
            "label": "Network",
            "role": "textbox",
            "widget": "input",
            "required": False,
            "value": "",
            "options": None,
        },
    ],
    "apply_controls": [
        {
            "key": "apply",
            "label": "Apply to Device",
            "role": "button",
        }
    ],
    "unmapped": [],
}


def _stub_basics(monkeypatch) -> None:
    """Patch _search_docs, webui_open, webui_perceive, read_tools, and
    close_all_sessions to standard stubs for propose-path tests."""
    monkeypatch.setattr(tr, "_search_docs", lambda **kw: _RAG_RESULT)
    monkeypatch.setattr(
        tr,
        "webui_open",
        lambda **kw: {"session_id": "sess_atlas_001", "view": {}},
    )
    monkeypatch.setattr(
        tr,
        "webui_perceive",
        lambda **kw: {"view": _PERCEIVE_VIEW, "session_id": "sess_atlas_001"},
    )
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "")
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)
    # Fingerprint: show_version will be called via _device_fingerprint_for_session;
    # stub it so no real SSH fires.
    monkeypatch.setattr(tr.read_tools, "show_version", lambda: {})


# ---------------------------------------------------------------------------
# _device_fingerprint_for_session
# ---------------------------------------------------------------------------


def test_device_fingerprint_returns_unknown_on_error(monkeypatch):
    """When show_version raises, the helper must return 'unknown__unknown'."""
    monkeypatch.setattr(
        tr.read_tools, "show_version", lambda: (_ for _ in ()).throw(OSError("ssh down"))
    )
    result = tr._device_fingerprint_for_session()
    assert result == "unknown__unknown"


def test_device_fingerprint_returns_slug_on_success(monkeypatch):
    """When show_version returns a dict, device_fingerprint is called and
    the helper returns the formatted slug string."""
    monkeypatch.setattr(
        tr.read_tools,
        "show_version",
        lambda: {"HARDWARE": ["C1111-4P"], "VERSION": "17.6.3a"},
    )
    result = tr._device_fingerprint_for_session()
    # Expect slugified model__version — e.g. "c1111-4p__17-6-3a"
    assert "__" in result
    assert result != "unknown__unknown"


# ---------------------------------------------------------------------------
# _atlas_from_view
# ---------------------------------------------------------------------------


def test_atlas_from_view_builds_fields():
    """The helper must convert the perceive view's fields list into a RouteAtlas
    with callable field_by_key."""
    atlas = tr._atlas_from_view(_PERCEIVE_VIEW)
    assert atlas.field_by_key("dhcp.pool_name") is not None
    assert atlas.field_by_key("dhcp.network") is not None
    assert atlas.field_by_key("nonexistent") is None


def test_atlas_from_view_builds_apply_controls():
    atlas = tr._atlas_from_view(_PERCEIVE_VIEW)
    assert len(atlas.apply_controls) == 1
    assert atlas.apply_controls[0].key == "apply"
    assert atlas.apply_controls[0].label == "Apply to Device"


def test_atlas_from_view_ignores_fields_without_key():
    view = {
        "fields": [
            {"key": "", "label": "Bad", "role": "textbox", "widget": "input"},
            {"key": "good.key", "label": "Good", "role": "textbox", "widget": "input"},
        ],
        "apply_controls": [],
    }
    atlas = tr._atlas_from_view(view)
    assert len(atlas.fields) == 1
    assert atlas.fields[0].key == "good.key"


# ---------------------------------------------------------------------------
# _propose_webui_configure_atlas — bad parameters
# ---------------------------------------------------------------------------


def test_propose_atlas_missing_intent():
    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "  ", "webui_path": "/webui/#/dhcp"},
    )
    assert result["error"] == "bad_parameters"


def test_propose_atlas_missing_webui_path():
    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool", "webui_path": ""},
    )
    assert result["error"] == "bad_parameters"


# ---------------------------------------------------------------------------
# _propose_webui_configure_atlas — happy path
# ---------------------------------------------------------------------------


def test_propose_atlas_happy_path(monkeypatch):
    """Perceive view with 2 fields → draft_atlas_plan returns a 2-field plan →
    awaiting_approval with display steps carrying field_key + final apply step.
    propose_action is called with params.plan containing field_key steps.
    """
    from backend.orchestration.confirmations import get_action

    drafted = {
        "plan": [
            {"field_key": "dhcp.pool_name", "value": "CORP"},
            {"field_key": "dhcp.network", "value": "10.0.0.0"},
        ],
        "verify_text": "Pool CORP",
        "risk": "Adds DHCP pool CORP.",
        "equivalent_cli_commands": ["ip dhcp pool CORP", "network 10.0.0.0 255.255.255.0"],
        "validation_errors": [],
    }
    monkeypatch.setattr(tr, "draft_atlas_plan", lambda *a, **kw: drafted)
    _stub_basics(monkeypatch)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool CORP 10.0.0.0/24", "webui_path": "/webui/#/dhcp"},
    )

    assert result["status"] == "awaiting_approval"
    assert result["action_id"].startswith("act_")
    assert result["execute_tool"] == "webui_configure"

    plan = result["preview"]["plan"]
    # 2 field steps + 1 apply step = 3
    assert len(plan) == 3, f"expected 3 display steps, got {len(plan)}: {plan}"

    # Field steps must carry field_key
    field_steps = [s for s in plan if "field_key" in s]
    assert len(field_steps) == 2
    fkeys = [s["field_key"] for s in field_steps]
    assert "dhcp.pool_name" in fkeys
    assert "dhcp.network" in fkeys

    # Apply step must carry apply_key (not field_key)
    apply_step = [s for s in plan if "apply_key" in s]
    assert len(apply_step) == 1
    assert apply_step[0]["apply_key"] == "apply"
    assert apply_step[0]["action"] == "click"

    # Stored action params must contain plan + apply_key
    stored = get_action(result["action_id"])
    assert "plan" in stored["params"]
    assert stored["params"]["apply_key"] == "apply"
    assert stored["params"]["session_id"] == "sess_atlas_001"
    assert stored["params"]["verify_text"] == "Pool CORP"


def test_propose_atlas_opens_form_via_open_form_control(monkeypatch):
    """List page (no fields) carrying open_form_control → propose clicks the
    Add button via webui_open_form_for_planning, re-perceives the now-open
    form, and plans against it.

    Deep-audit regression: reconcile must surface open_form_control in the view
    (it is not a field), else the form never opens and the plan is empty —
    which would break OSPF/DHCP at the very first smoke.
    """
    _stub_basics(monkeypatch)
    list_view = {
        "route": "/webui/#/dhcp",
        "page_title": "DHCP",
        "fields": [],
        "apply_controls": [],
        "unmapped": [],
        "open_form_control": {"key": "add", "label": "Add", "role": "button"},
    }
    calls = {"perceive": 0}

    def _perceive(**kw):
        calls["perceive"] += 1
        # First perceive = list page; after the Add click the form fields appear.
        view = list_view if calls["perceive"] == 1 else _PERCEIVE_VIEW
        return {"view": view, "session_id": "sess_atlas_001"}

    monkeypatch.setattr(tr, "webui_perceive", _perceive)

    open_form_intents: list = []

    def _open_form(session_id, intent):
        open_form_intents.append(intent)
        return {"ok": True, "view": _PERCEIVE_VIEW, "session_id": session_id}

    monkeypatch.setattr(tr, "webui_open_form_for_planning", _open_form)
    monkeypatch.setattr(
        tr,
        "draft_atlas_plan",
        lambda *a, **kw: {
            "plan": [{"field_key": "dhcp.pool_name", "value": "MYPOOL"}],
            "verify_text": "MYPOOL",
            "risk": "Adds DHCP pool MYPOOL.",
            "equivalent_cli_commands": [],
            "validation_errors": [],
        },
    )

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool MYPOOL", "webui_path": "/webui/#/dhcp"},
    )

    assert result["status"] == "awaiting_approval"
    # The Add button was clicked to reveal the form.
    assert len(open_form_intents) == 1
    assert open_form_intents[0]["name"] == "Add"
    assert open_form_intents[0]["action"] == "click"
    # Perceived twice: list page, then the open form.
    assert calls["perceive"] == 2


def test_propose_atlas_opens_form_despite_stray_list_field(monkeypatch):
    """P0-open-form-gate: a Cisco list page that leaks a STRAY grid-row field
    (DHCP's "Monitoring" row-checkbox) plus an open_form_control and NO Apply
    control must STILL click Add and re-perceive the real form.

    Regression lock: the previous gate (``not _view_fields and not _has_submit``)
    never fired because the stray checkbox made ``not _view_fields`` False, so
    the form never opened and the plan came back empty → CLI fallback.  The new
    gate keys only on ``not _has_submit`` (no Apply control == form not open).
    """
    _stub_basics(monkeypatch)
    # List page: one stray grid-row checkbox, NO apply controls, an Add button.
    list_view_with_stray = {
        "route": "/webui/#/dhcp",
        "page_title": "DHCP Pools",
        "fields": [
            {
                "key": "checked",
                "label": "Monitoring",
                "role": "checkbox",
                "widget": "checkbox",
                "required": False,
                "value": "",
                "options": None,
            }
        ],
        "apply_controls": [],
        "unmapped": [],
        "open_form_control": {"key": "add", "label": "Add", "role": "button"},
    }
    calls = {"perceive": 0}

    def _perceive(**kw):
        calls["perceive"] += 1
        view = list_view_with_stray if calls["perceive"] == 1 else _PERCEIVE_VIEW
        return {"view": view, "session_id": "sess_atlas_001"}

    monkeypatch.setattr(tr, "webui_perceive", _perceive)

    open_form_intents: list = []

    def _open_form(session_id, intent):
        open_form_intents.append(intent)
        return {"ok": True, "view": _PERCEIVE_VIEW, "session_id": session_id}

    monkeypatch.setattr(tr, "webui_open_form_for_planning", _open_form)
    monkeypatch.setattr(
        tr,
        "draft_atlas_plan",
        lambda *a, **kw: {
            "plan": [{"field_key": "dhcp.pool_name", "value": "MYPOOL"}],
            "verify_text": "MYPOOL",
            "risk": "Adds DHCP pool MYPOOL.",
            "equivalent_cli_commands": [],
            "validation_errors": [],
        },
    )

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool MYPOOL", "webui_path": "/webui/#/dhcp"},
    )

    assert result["status"] == "awaiting_approval"
    # The Add button was clicked exactly once despite the stray field.
    assert len(open_form_intents) == 1
    assert open_form_intents[0]["name"] == "Add"
    assert open_form_intents[0]["action"] == "click"
    # Perceived twice: list page (with stray field), then the open form.
    assert calls["perceive"] == 2


def test_propose_atlas_skips_add_when_apply_present(monkeypatch):
    """P0-open-form-gate OSPF regression guard: when an Apply/submit control is
    already visible (form already open after a re-perceive), the Add click must
    NOT fire — otherwise OSPF would double-Add.
    """
    _stub_basics(monkeypatch)
    # Open form: real fields + an Apply control + a (stale) open_form_control.
    open_form_view = {
        "route": "/webui/#/OSPF",
        "page_title": "OSPF",
        "fields": [
            {
                "key": "dhcp.pool_name",
                "label": "Process ID",
                "role": "textbox",
                "widget": "input",
                "required": True,
                "value": "",
                "options": None,
            }
        ],
        "apply_controls": [{"key": "apply", "label": "Apply to Device", "role": "button"}],
        "unmapped": [],
        "open_form_control": {"key": "add", "label": "Add", "role": "button"},
    }
    monkeypatch.setattr(
        tr,
        "webui_perceive",
        lambda **kw: {"view": open_form_view, "session_id": "sess_atlas_001"},
    )

    open_form_calls: list = []

    def _open_form(session_id, intent):
        open_form_calls.append(intent)
        return {"ok": True, "view": open_form_view, "session_id": session_id}

    monkeypatch.setattr(tr, "webui_open_form_for_planning", _open_form)
    monkeypatch.setattr(
        tr,
        "draft_atlas_plan",
        lambda *a, **kw: {
            "plan": [{"field_key": "dhcp.pool_name", "value": "100"}],
            "verify_text": "100",
            "risk": "Sets OSPF process id.",
            "equivalent_cli_commands": [],
            "validation_errors": [],
        },
    )

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "set OSPF process id 100", "webui_path": "/webui/#/OSPF"},
    )

    assert result["status"] == "awaiting_approval"
    # Apply control already present → form is open → Add must NOT be clicked.
    assert open_form_calls == [], "Add must not be clicked when an Apply control is already visible"


def test_propose_atlas_empty_plan_returns_intent_not_mappable(monkeypatch):
    """draft_atlas_plan returns empty plan → error: intent_not_mappable.

    P2-empty-plan-visibility: a WARNING breadcrumb
    (propose_webui_configure_atlas_empty_plan) carrying field_keys + form_opened
    must be emitted so the WebUI->CLI fallback is diagnosable from logs alone.
    """
    drafted = {
        "plan": [],
        "verify_text": None,
        "risk": "No matching fields found for this intent.",
        "equivalent_cli_commands": [],
        "validation_errors": [{"field_key": "stray", "reason": "unknown_field_key"}],
    }
    monkeypatch.setattr(tr, "draft_atlas_plan", lambda *a, **kw: drafted)
    _stub_basics(monkeypatch)

    warn_events: list[tuple[str, dict]] = []
    orig_warning = tr.log.warning

    def _capture_warning(event, *a, **kw):
        warn_events.append((event, kw))
        return orig_warning(event, *a, **kw)

    monkeypatch.setattr(tr.log, "warning", _capture_warning)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure OSPF", "webui_path": "/webui/#/dhcp"},
    )

    assert result["error"] == "intent_not_mappable"

    empty_plan_warnings = [
        kw for (event, kw) in warn_events if event == "propose_webui_configure_atlas_empty_plan"
    ]
    assert len(empty_plan_warnings) == 1, "empty-plan WARNING breadcrumb must be emitted"
    breadcrumb = empty_plan_warnings[0]
    assert "field_keys" in breadcrumb
    assert "form_opened" in breadcrumb
    # _PERCEIVE_VIEW has dhcp.pool_name + dhcp.network as fields.
    assert "dhcp.pool_name" in breadcrumb["field_keys"]
    assert breadcrumb["form_opened"] is False  # an Apply control is present → no Add click
    assert "No matching" in result["message"]


def test_propose_atlas_closes_session_on_draft_failed(monkeypatch):
    """draft_atlas_plan raises RuntimeError → close_all_sessions called."""

    def _boom(*a, **kw):
        raise RuntimeError("atlas planner LLM returned non-JSON: ...")

    monkeypatch.setattr(tr, "draft_atlas_plan", _boom)
    _stub_basics(monkeypatch)

    close_calls: list[int] = []
    monkeypatch.setattr(tr, "close_all_sessions", lambda: close_calls.append(1))

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool CORP 10.0.0.0/24", "webui_path": "/webui/#/dhcp"},
    )

    assert result["error"] == "draft_failed"
    assert len(close_calls) == 1


def test_propose_atlas_closes_session_on_intent_not_mappable(monkeypatch):
    """Empty drafted plan → close_all_sessions called exactly once."""
    drafted = {
        "plan": [],
        "verify_text": None,
        "risk": "Can't map",
        "equivalent_cli_commands": [],
        "validation_errors": [],
    }
    monkeypatch.setattr(tr, "draft_atlas_plan", lambda *a, **kw: drafted)

    close_calls: list[int] = []
    monkeypatch.setattr(tr, "close_all_sessions", lambda: close_calls.append(1))
    monkeypatch.setattr(tr, "_search_docs", lambda **kw: _RAG_RESULT)
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_x"})
    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "")
    monkeypatch.setattr(tr.read_tools, "show_version", lambda: {})

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "configure OSPF", "webui_path": "/webui/#/dhcp"},
    )

    assert result["error"] == "intent_not_mappable"
    assert len(close_calls) == 1


def test_propose_atlas_perceive_error_closes_session(monkeypatch):
    """webui_perceive returns error → close_all_sessions called, error surfaced."""
    monkeypatch.setattr(tr, "_search_docs", lambda **kw: _RAG_RESULT)
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_err"})
    monkeypatch.setattr(
        tr,
        "webui_perceive",
        lambda **kw: {"error": "webui_perceive_failed", "message": "child crashed"},
    )
    monkeypatch.setattr(tr.read_tools, "show_version", lambda: {})

    close_calls: list[int] = []
    monkeypatch.setattr(tr, "close_all_sessions", lambda: close_calls.append(1))

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool", "webui_path": "/webui/#/dhcp"},
    )

    assert result["error"] == "webui_perceive_failed"
    assert len(close_calls) == 1


# ---------------------------------------------------------------------------
# _propose_webui_configure_atlas — combobox action mapping
# ---------------------------------------------------------------------------


def test_propose_atlas_combobox_field_maps_to_select_action(monkeypatch):
    """A kendo_combobox field must produce action='select' in the display step."""
    combobox_view = {
        "route": "/webui/#/x",
        "page_title": "X",
        "fields": [
            {
                "key": "x.subnet",
                "label": "Subnet Mask",
                "role": "combobox",
                "widget": "kendo_combobox",
                "required": False,
                "value": "",
                "options": ["255.255.255.0", "255.255.0.0"],
            }
        ],
        "apply_controls": [{"key": "apply", "label": "Apply", "role": "button"}],
        "unmapped": [],
    }
    drafted = {
        "plan": [{"field_key": "x.subnet", "value": "255.255.255.0"}],
        "verify_text": None,
        "risk": "Sets subnet.",
        "equivalent_cli_commands": [],
        "validation_errors": [],
    }
    monkeypatch.setattr(tr, "_search_docs", lambda **kw: _RAG_RESULT)
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_cb"})
    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": combobox_view})
    monkeypatch.setattr(tr.read_tools, "show_running_config", lambda: "")
    monkeypatch.setattr(tr.read_tools, "show_version", lambda: {})
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)
    monkeypatch.setattr(tr, "draft_atlas_plan", lambda *a, **kw: drafted)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "set subnet to /24", "webui_path": "/webui/#/x"},
    )

    assert result["status"] == "awaiting_approval"
    field_steps = [s for s in result["preview"]["plan"] if "field_key" in s]
    assert len(field_steps) == 1
    assert field_steps[0]["action"] == "select"


# ---------------------------------------------------------------------------
# _propose_webui_configure_atlas — conflict detection
# ---------------------------------------------------------------------------


def test_propose_atlas_conflict_detection_populates_preview_meta(monkeypatch):
    """When equivalent_cli_commands match running-config, preview_meta is set."""
    from backend.orchestration.confirmations import get_action

    drafted = {
        "plan": [{"field_key": "dhcp.pool_name", "value": "CORP"}],
        "verify_text": "CORP",
        "risk": "Adds DHCP pool.",
        "equivalent_cli_commands": ["ip dhcp pool CORP"],
        "validation_errors": [],
    }
    monkeypatch.setattr(tr, "draft_atlas_plan", lambda *a, **kw: drafted)
    monkeypatch.setattr(tr, "_search_docs", lambda **kw: _RAG_RESULT)
    monkeypatch.setattr(tr, "webui_open", lambda **kw: {"session_id": "sess_conf"})
    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr.read_tools,
        "show_running_config",
        lambda: "!\nip dhcp pool CORP\n network 10.0.0.0 255.255.255.0\n!\n",
    )
    monkeypatch.setattr(tr.read_tools, "show_version", lambda: {})
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    result = tr.execute_tool(
        "propose_webui_configure",
        {"intent": "add DHCP pool CORP", "webui_path": "/webui/#/dhcp"},
    )

    assert result["status"] == "awaiting_approval"
    assert result["preview_meta"] is not None
    assert result["preview_meta"]["existing_entity"] == "ip dhcp pool CORP"

    stored = get_action(result["action_id"])
    # Conflict must NOT leak into params (executor splat safety)
    assert "existing_entity" not in stored["params"]
    assert stored["preview_meta"]["existing_entity"] == "ip dhcp pool CORP"


# ---------------------------------------------------------------------------
# _webui_configure_atlas — requires approval
# ---------------------------------------------------------------------------


def test_webui_configure_atlas_requires_approval():
    """Calling webui_configure without an APPROVED action_id → not_approved."""
    result = tr.execute_tool("webui_configure", {"action_id": "act_nonexistent"})
    assert result["error"] == "not_approved"


# ---------------------------------------------------------------------------
# _webui_configure_atlas — happy path (NO re-plan regression lock)
# ---------------------------------------------------------------------------


def _make_atlas_action(*, verify_text: str | None = "Pool CORP") -> str:
    """Register and approve a minimal atlas-path action, returning action_id."""
    plan = [
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Pool Name"},
            "value": "CORP",
            "field_key": "dhcp.pool_name",
        },
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Network"},
            "value": "10.0.0.0",
            "field_key": "dhcp.network",
        },
        {
            "action": "click",
            "intent": {"role": "button", "name": "Apply to Device"},
            "value": None,
            "apply_key": "apply",
        },
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "add DHCP pool CORP",
            "webui_path": "/webui/#/dhcp",
            "plan": plan,
            "verify_text": verify_text,
            "session_id": "sess_exec_001",
            "device_fingerprint": "c1111-4p__17-6-3a",
            "equivalent_cli_commands": [],
            "apply_key": "apply",
        },
    )
    approve_action(action_id)
    return action_id


def test_webui_configure_atlas_happy_path(monkeypatch):
    """2 field steps + apply → act_field called per field, apply_control called
    once, verify_a11y called, mark_executed called, POST-snapshot taken.
    CRITICAL: draft_atlas_plan / draft_plan MUST NOT be called."""
    action_id = _make_atlas_action()

    act_field_calls: list[dict] = []
    apply_control_calls: list[dict] = []
    verify_calls: list[dict] = []
    executed_ids: list[str] = []
    snapshot_calls: list[tuple] = []
    draft_atlas_plan_calls: list = []

    def _fake_act_field(session_id, field_key, value, action_id):
        act_field_calls.append({"field_key": field_key, "value": value})
        return {"ok": True, "field_key": field_key}

    def _fake_apply_control(session_id, action_id, key=None):
        apply_control_calls.append({"key": key})
        return {"ok": True}

    def _fake_verify_a11y(session_id, contains):
        verify_calls.append({"contains": contains})
        return {"present": True}

    def _fake_take_snapshot(action_id, phase):
        snapshot_calls.append((action_id, phase))

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(tr, "webui_act_field", _fake_act_field)
    monkeypatch.setattr(tr, "webui_apply_control", _fake_apply_control)
    monkeypatch.setattr(tr, "webui_verify_a11y", _fake_verify_a11y)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    # Intercept draft_atlas_plan — must NOT be called at execute
    monkeypatch.setattr(
        tr, "draft_atlas_plan", lambda *a, **kw: draft_atlas_plan_calls.append(1) or {}
    )

    import backend.cli_agent.snapshots as snaps
    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})
    monkeypatch.setattr(snaps, "take_snapshot", _fake_take_snapshot)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result.get("ok") is True, f"expected ok=True, got {result}"

    # Both field steps called via act_field
    assert len(act_field_calls) == 2
    field_keys_called = {c["field_key"] for c in act_field_calls}
    assert "dhcp.pool_name" in field_keys_called
    assert "dhcp.network" in field_keys_called

    # Apply called once with the right key
    assert len(apply_control_calls) == 1
    assert apply_control_calls[0]["key"] == "apply"

    # Verify called
    assert len(verify_calls) == 1
    assert verify_calls[0]["contains"] == "Pool CORP"

    # mark_executed called
    assert len(executed_ids) == 1
    assert executed_ids[0] == action_id

    # PRE-snapshot taken before any field act; POST after a successful apply.
    pre_snaps = [s for s in snapshot_calls if s[1] == "pre"]
    post_snaps = [s for s in snapshot_calls if s[1] == "post"]
    assert len(pre_snaps) == 1, f"expected one pre-snapshot, got {snapshot_calls}"
    assert pre_snaps[0][0] == action_id
    assert len(post_snaps) == 1
    # Ordering: pre is the FIRST snapshot, post is the LAST.
    assert snapshot_calls[0][1] == "pre"
    assert snapshot_calls[-1][1] == "post"

    # NO re-plan — the inner_plan_empty regression lock
    assert draft_atlas_plan_calls == [], "draft_atlas_plan must NOT be called at execute time"


def test_webui_configure_atlas_pre_snapshot_failure_is_best_effort(monkeypatch):
    """P1-pre-snapshot: a take_snapshot('pre') raising must NOT abort the
    operator-approved write — apply still runs and the action is executed."""
    action_id = _make_atlas_action()

    executed_ids: list[str] = []
    apply_calls: list = []
    snapshot_phases: list[str] = []

    def _fake_take_snapshot(action_id, phase):
        snapshot_phases.append(phase)
        if phase == "pre":
            raise OSError("ssh transport closed")

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr,
        "webui_act_field",
        lambda session_id, field_key, value, action_id: {"ok": True, "field_key": field_key},
    )
    monkeypatch.setattr(
        tr,
        "webui_apply_control",
        lambda **kw: apply_calls.append(1) or {"ok": True},
    )
    monkeypatch.setattr(tr, "webui_verify_a11y", lambda **kw: {"present": True})
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.cli_agent.snapshots as snaps
    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})
    monkeypatch.setattr(snaps, "take_snapshot", _fake_take_snapshot)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result.get("ok") is True, f"pre-snapshot failure must not abort write; got {result}"
    assert "pre" in snapshot_phases  # pre was attempted (and raised)
    assert len(apply_calls) == 1  # apply still ran
    assert executed_ids == [action_id]


# ---------------------------------------------------------------------------
# _webui_configure_atlas — field step fails twice → no_progress
# ---------------------------------------------------------------------------


def test_webui_configure_atlas_step_fails_twice_no_progress(monkeypatch):
    """If the SAME (field_key, failure_reason) pair appears twice in a single
    plan batch, no_progress fires and apply is NOT called.

    The atlas executor processes ALL field steps in one pass (no break on
    first failure).  A plan that lists the same field_key twice with the same
    failure triggers the convergence guard (count >= 2).
    """
    # Build a plan where the same field_key appears twice — both will fail
    # with the same failure_reason, triggering the no_progress guard.
    plan_double_fail = [
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Pool Name"},
            "value": "CORP",
            "field_key": "dhcp.pool_name",
        },
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Pool Name Again"},
            "value": "CORP2",
            "field_key": "dhcp.pool_name",  # same key — count reaches 2 → no_progress
        },
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "test",
            "webui_path": "/webui/#/dhcp",
            "plan": plan_double_fail,
            "verify_text": None,
            "session_id": "sess_double",
            "device_fingerprint": "unknown__unknown",
            "equivalent_cli_commands": [],
            "apply_key": "apply",
        },
    )
    approve_action(action_id)

    apply_calls: list = []
    failed_ids: list[str] = []

    def _fake_act_field(session_id, field_key, value, action_id):
        # Both occurrences of dhcp.pool_name fail with the same reason.
        return {"ok": False, "failure_reason": "unmapped_field", "field_key": field_key}

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(tr, "webui_act_field", _fake_act_field)
    monkeypatch.setattr(
        tr,
        "webui_apply_control",
        lambda **kw: apply_calls.append(1) or {"ok": True},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid, *a, **kw: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result["error"] == "no_progress", f"expected no_progress, got {result}"
    assert result["field_key"] == "dhcp.pool_name"
    assert result["failure_count"] == 2
    assert len(apply_calls) == 0, "apply must NOT be called when no_progress fires"
    assert len(failed_ids) == 1


# ---------------------------------------------------------------------------
# _webui_configure_atlas — unmapped_field treated as step failure
# ---------------------------------------------------------------------------


def test_webui_configure_atlas_unmapped_field_is_step_failure(monkeypatch):
    """act_field returning failure_reason='unmapped_field' must be treated as
    a step failure — no vision call, no special handling."""
    action_id = _make_atlas_action(verify_text=None)

    apply_calls: list = []
    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr,
        "webui_act_field",
        lambda session_id, field_key, value, action_id: {
            "ok": False,
            "failure_reason": "unmapped_field",
            "field_key": field_key,
        },
    )
    monkeypatch.setattr(
        tr,
        "webui_apply_control",
        lambda **kw: apply_calls.append(1) or {"ok": True},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid, *a, **kw: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    # Should be a step_failed error
    assert result.get("error") in ("step_failed", "no_progress"), f"unexpected result: {result}"
    # Apply must NOT be called when a field step fails
    assert len(apply_calls) == 0


# ---------------------------------------------------------------------------
# _webui_configure_atlas — apply click_timeout_unsafe_retry → mark_failed, not retried
# ---------------------------------------------------------------------------


def test_webui_configure_atlas_apply_click_timeout_unsafe_retry(monkeypatch):
    """apply_control returning failure_reason='click_timeout_unsafe_retry' must
    cause mark_failed and must NOT be retried."""
    action_id = _make_atlas_action()

    apply_call_count = [0]
    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr,
        "webui_act_field",
        lambda session_id, field_key, value, action_id: {"ok": True, "field_key": field_key},
    )

    def _fake_apply(session_id, action_id, key=None):
        apply_call_count[0] += 1
        return {"ok": False, "failure_reason": "click_timeout_unsafe_retry"}

    monkeypatch.setattr(tr, "webui_apply_control", _fake_apply)
    monkeypatch.setattr(tr, "webui_verify_a11y", lambda **kw: {"present": False})
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_failed", lambda aid, *a, **kw: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result["error"] == "apply_failed"
    assert "click_timeout_unsafe_retry" in result.get(
        "failure_reason", ""
    ) or "click_timeout_unsafe_retry" in str(
        result.get("apply_result", {}).get("failure_reason", "")
    )
    # apply called exactly once — NOT retried
    assert apply_call_count[0] == 1
    assert len(failed_ids) == 1


# ---------------------------------------------------------------------------
# _webui_configure_atlas — verify_failed does not mark_executed
# ---------------------------------------------------------------------------


def test_webui_configure_atlas_verify_failed_does_not_mark_executed(monkeypatch):
    """If webui_verify_a11y returns present=False, the action must NOT be marked
    executed and an error dict must be returned."""
    action_id = _make_atlas_action(verify_text="Pool CORP")

    executed_ids: list[str] = []
    failed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr,
        "webui_act_field",
        lambda session_id, field_key, value, action_id: {"ok": True, "field_key": field_key},
    )
    monkeypatch.setattr(
        tr,
        "webui_apply_control",
        lambda **kw: {"ok": True},
    )
    monkeypatch.setattr(
        tr,
        "webui_verify_a11y",
        lambda **kw: {"present": False},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})
    monkeypatch.setattr(confs, "mark_failed", lambda aid, *a, **kw: failed_ids.append(aid) or {})

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result["error"] == "verify_failed"
    assert executed_ids == [], "mark_executed must NOT be called when verify fails"
    assert len(failed_ids) == 1


# ---------------------------------------------------------------------------
# _webui_configure_atlas — no verify_text → mark_executed on success
# ---------------------------------------------------------------------------


def test_webui_configure_atlas_no_verify_text_succeeds(monkeypatch):
    """When verify_text is None, the function must still mark_executed after
    a successful apply (no verify call needed)."""
    action_id = _make_atlas_action(verify_text=None)

    executed_ids: list[str] = []
    verify_calls: list = []

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr,
        "webui_act_field",
        lambda session_id, field_key, value, action_id: {"ok": True, "field_key": field_key},
    )
    monkeypatch.setattr(tr, "webui_apply_control", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr,
        "webui_verify_a11y",
        lambda **kw: verify_calls.append(1) or {"present": True},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.cli_agent.snapshots as snaps
    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})
    monkeypatch.setattr(snaps, "take_snapshot", lambda *a: None)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result.get("ok") is True
    assert len(executed_ids) == 1
    # verify_a11y must NOT be called when there is no verify_text
    assert len(verify_calls) == 0


# ---------------------------------------------------------------------------
# _webui_configure_atlas — verify falls back to atlas success_signal
# ---------------------------------------------------------------------------


def _make_atlas_action_with_signal(
    *, verify_text: str | None, success_signal_contains: str | None
) -> str:
    """Register+approve an atlas action carrying an optional success_signal."""
    plan = [
        {
            "action": "fill",
            "intent": {"role": "textbox", "name": "Pool Name"},
            "value": "CORP",
            "field_key": "dhcp.pool_name",
        },
        {
            "action": "click",
            "intent": {"role": "button", "name": "Apply to Device"},
            "value": None,
            "apply_key": "apply",
        },
    ]
    action_id = propose_action(
        "webui_configure",
        {
            "intent": "add DHCP pool CORP",
            "webui_path": "/webui/#/dhcp",
            "plan": plan,
            "verify_text": verify_text,
            "success_signal_contains": success_signal_contains,
            "session_id": "sess_signal_001",
            "device_fingerprint": "c1111-4p__17-6-3a",
            "equivalent_cli_commands": [],
            "apply_key": "apply",
        },
    )
    approve_action(action_id)
    return action_id


def test_execute_verify_falls_back_to_success_signal(monkeypatch):
    """P1-verify-fallback: verify_text=None but success_signal_contains='success'
    → verify_a11y is called with contains='success' (not skipped)."""
    action_id = _make_atlas_action_with_signal(verify_text=None, success_signal_contains="success")

    verify_calls: list[dict] = []
    executed_ids: list[str] = []

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr,
        "webui_act_field",
        lambda session_id, field_key, value, action_id: {"ok": True, "field_key": field_key},
    )
    monkeypatch.setattr(tr, "webui_apply_control", lambda **kw: {"ok": True})

    def _fake_verify(session_id, contains):
        verify_calls.append({"contains": contains})
        return {"present": True}

    monkeypatch.setattr(tr, "webui_verify_a11y", _fake_verify)
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    import backend.cli_agent.snapshots as snaps
    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})
    monkeypatch.setattr(snaps, "take_snapshot", lambda *a: None)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result.get("ok") is True
    assert result.get("verified") is True
    assert len(verify_calls) == 1
    assert verify_calls[0]["contains"] == "success"
    assert executed_ids == [action_id]


def test_execute_unverified_flag_when_no_target(monkeypatch):
    """P1-verify-fallback: verify_text=None AND no success_signal_contains →
    mark_executed (apply succeeded) BUT result.verified is False and a
    webui_configure_atlas_unverified WARNING is emitted."""
    action_id = _make_atlas_action_with_signal(verify_text=None, success_signal_contains=None)

    executed_ids: list[str] = []
    verify_calls: list = []
    warnings: list[str] = []

    monkeypatch.setattr(tr, "webui_perceive", lambda **kw: {"view": _PERCEIVE_VIEW})
    monkeypatch.setattr(
        tr,
        "webui_act_field",
        lambda session_id, field_key, value, action_id: {"ok": True, "field_key": field_key},
    )
    monkeypatch.setattr(tr, "webui_apply_control", lambda **kw: {"ok": True})
    monkeypatch.setattr(
        tr,
        "webui_verify_a11y",
        lambda **kw: verify_calls.append(1) or {"present": True},
    )
    monkeypatch.setattr(tr, "close_all_sessions", lambda: None)

    # Capture WARNING-level log events via the module logger.
    orig_warning = tr.log.warning

    def _capture_warning(event, *a, **kw):
        warnings.append(event)
        return orig_warning(event, *a, **kw)

    monkeypatch.setattr(tr.log, "warning", _capture_warning)

    import backend.cli_agent.snapshots as snaps
    import backend.orchestration.confirmations as confs

    monkeypatch.setattr(confs, "mark_executed", lambda aid: executed_ids.append(aid) or {})
    monkeypatch.setattr(snaps, "take_snapshot", lambda *a: None)

    result = tr.execute_tool("webui_configure", {"action_id": action_id})

    assert result.get("ok") is True
    assert result.get("verified") is False
    assert executed_ids == [action_id]
    # verify_a11y must NOT be called when there is no target at all.
    assert len(verify_calls) == 0
    assert "webui_configure_atlas_unverified" in warnings
