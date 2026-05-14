"""Unit tests for backend.webui_agent.flows.add_access_vlan — subprocess-isolated.

The Playwright portion of the flow now runs in a child Python process
(see backend/webui_agent/_playwright_subprocess.py). These tests mock
the parent-side `run_flow_in_subprocess` helper, so they verify
EVERYTHING the parent does (guard, snapshots, verify, mark_executed/
mark_failed, pool invalidation, result shape) without touching real
Playwright OR spawning real subprocesses.

For tests of the Playwright steps themselves, see the page-object tests
in test_webui_vlan_page.py and test_webui_login.py — those are still
mocked-Playwright unit tests but live a layer down.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import backend.webui_agent.flows.add_access_vlan as flow_mod
from backend.orchestration.confirmations import (
    NotApproved,
    approve_action,
    propose_action,
)

# All tests in this module exercise the WebUI agent layer (Playwright
# runs in a mocked subprocess; no real browser launches). Tagged with
# the `webui` marker so `pytest -m 'not webui'` skips them.
pytestmark = pytest.mark.webui

# `_clean_actions` fixture is in tests/conftest.py (autouse).


@pytest.fixture()
def _isolated_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point settings at a temp directory so screenshots / snapshots don't bleed."""
    fake = MagicMock()
    fake.artifacts_dir = tmp_path
    fake.router_host = "10.0.0.1"
    fake.router_ssh_user = "admin"
    fake.router_ssh_password = "pass"
    monkeypatch.setattr(flow_mod, "get_settings", lambda: fake)
    return tmp_path


@pytest.fixture()
def _stub_pool(monkeypatch: pytest.MonkeyPatch):
    mock_pool = MagicMock()
    monkeypatch.setattr(flow_mod, "pool", mock_pool)
    return mock_pool


@pytest.fixture()
def _stub_snapshots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    snap_call_log = []

    def fake_take(action_id: str, phase: str) -> Path:
        snap_call_log.append((action_id, phase))
        d = tmp_path / f"snap-{action_id}-{phase}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(flow_mod, "take_snapshot", fake_take)
    return snap_call_log


@pytest.fixture()
def _stub_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Replace run_flow_in_subprocess with a configurable mock.

    Default behaviour: return a successful `{"screenshots": <path>}`
    so the parent proceeds to verify + post-snapshot. Tests that need
    failure can override `.side_effect` or `.return_value` on the
    returned mock.
    """
    screenshots_dir = tmp_path / "screens"
    screenshots_dir.mkdir(exist_ok=True)
    mock = MagicMock(return_value={"screenshots": str(screenshots_dir)})
    monkeypatch.setattr(flow_mod, "run_flow_in_subprocess", mock)
    return mock


# ---------------------------------------------------------------------------
# Approval gate — refuse without APPROVED action
# ---------------------------------------------------------------------------


def test_refuses_without_approval(_isolated_artifacts, _stub_subprocess):
    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    # NOT approved
    with pytest.raises(NotApproved):
        flow_mod.add_access_vlan_via_webui(30, "OFFICE", action_id=action_id)
    _stub_subprocess.assert_not_called()


def test_refuses_for_unknown_action_id(_isolated_artifacts):
    with pytest.raises(NotApproved):
        flow_mod.add_access_vlan_via_webui(30, "OFFICE", action_id="act_nope")


# ---------------------------------------------------------------------------
# Happy path — full flow with mocked subprocess
# ---------------------------------------------------------------------------


def test_happy_path_returns_structured_result(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_vlan_exists", return_value=True):
        result = flow_mod.add_access_vlan_via_webui(30, "OFFICE", action_id=action_id)

    assert result["tool"] == "webui_add_access_vlan"
    assert result["vlan_id"] == 30
    assert result["vlan_name"] == "OFFICE"
    assert result["verified"] is True
    assert Path(result["snapshot_pre"]).exists()
    assert Path(result["snapshot_post"]).exists()
    assert Path(result["screenshots"]).exists()


def test_happy_path_passes_args_to_subprocess(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    """The parent must pass vlan_id / vlan_name / action_id / headless to the child."""
    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 42, "vlan_name": "DEV"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_vlan_exists", return_value=True):
        flow_mod.add_access_vlan_via_webui(42, "DEV", action_id=action_id, headless=True)

    _stub_subprocess.assert_called_once()
    flow_name, payload = _stub_subprocess.call_args.args
    assert flow_name == "add_access_vlan"
    assert payload["vlan_id"] == 42
    assert payload["vlan_name"] == "DEV"
    assert payload["action_id"] == action_id
    assert payload["headless"] is True


def test_happy_path_takes_pre_then_post_snapshots(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 99, "vlan_name": "TEST"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_vlan_exists", return_value=True):
        flow_mod.add_access_vlan_via_webui(99, "TEST", action_id=action_id)

    assert _stub_snapshots[0] == (action_id, "pre")
    assert _stub_snapshots[1] == (action_id, "post")


def test_happy_path_invalidates_connection_pool(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    """After WebUI add, the pooled SSH may have stale state. Pool must be
    invalidated before the verify CLI call so verify reads fresh."""
    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_vlan_exists", return_value=True):
        flow_mod.add_access_vlan_via_webui(30, "OFFICE", action_id=action_id)

    _stub_pool.invalidate.assert_called_once()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_verification_failure_raises_and_marks_failed(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    """WebUI clicked Save but CLI 'show vlan brief' doesn't list it → raise."""
    from backend.orchestration.confirmations import ActionState, get_action

    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    approve_action(action_id)

    with (
        patch.object(flow_mod, "verify_vlan_exists", return_value=False),
        pytest.raises(flow_mod.WebUIVerificationError),
    ):
        flow_mod.add_access_vlan_via_webui(30, "OFFICE", action_id=action_id)

    assert get_action(action_id)["state"] == ActionState.FAILED


def test_subprocess_failure_propagates_and_marks_failed(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    """If the child Playwright process raises (login fail / Playwright crash /
    selector timeout / etc.), the parent must mark_failed and re-raise so
    the dispatcher / route can translate it into a 500 + error banner."""
    from backend.orchestration.confirmations import ActionState, get_action
    from backend.webui_agent._subprocess import SubprocessFlowError

    action_id = propose_action("webui_add_access_vlan", {"vlan_id": 30, "vlan_name": "OFFICE"})
    approve_action(action_id)

    _stub_subprocess.side_effect = SubprocessFlowError(
        flow="add_access_vlan",
        error="WebUI login failed",
        exc_type="RuntimeError",
        stderr="(traceback omitted)",
    )

    with pytest.raises(SubprocessFlowError):
        flow_mod.add_access_vlan_via_webui(30, "OFFICE", action_id=action_id)

    assert get_action(action_id)["state"] == ActionState.FAILED
