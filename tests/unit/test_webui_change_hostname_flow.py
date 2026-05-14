"""Unit tests for backend.webui_agent.flows.change_hostname — subprocess-isolated.

The Playwright portion of the flow now runs in a child Python process
(see backend/webui_agent/_playwright_subprocess.py). These tests mock
the parent-side `run_flow_in_subprocess` helper, so they verify
EVERYTHING the parent does (guard, snapshots, verify, mark_executed/
mark_failed, pool invalidation, result shape) without touching real
Playwright OR spawning real subprocesses.

For tests of the Playwright steps themselves, see the page-object tests
in test_webui_hostname_page.py and test_webui_login.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import backend.webui_agent.flows.change_hostname as flow_mod
from backend.orchestration.confirmations import (
    NotApproved,
    approve_action,
    propose_action,
)

# All tests in this module exercise the WebUI agent layer (Playwright
# runs in a mocked subprocess; no real browser launches). Tagged with
# the `webui` marker so `pytest -m 'not webui'` skips them.
pytestmark = pytest.mark.webui


@pytest.fixture()
def _isolated_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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

    Default: returns a successful payload with `old_hostname` and a
    screenshots dir. Override `.side_effect` to simulate a child
    failure, or `.return_value` to simulate a different result shape.
    """
    screenshots_dir = tmp_path / "screens"
    screenshots_dir.mkdir(exist_ok=True)
    mock = MagicMock(
        return_value={
            "old_hostname": "c1111-lab",
            "screenshots": str(screenshots_dir),
        }
    )
    monkeypatch.setattr(flow_mod, "run_flow_in_subprocess", mock)
    return mock


# ---------------------------------------------------------------------------
# Approval gate — refuse without APPROVED action
# ---------------------------------------------------------------------------


def test_refuses_without_approval(_isolated_artifacts, _stub_subprocess):
    action_id = propose_action("webui_set_hostname", {"new_name": "LAB-R1"})
    # NOT approved
    with pytest.raises(NotApproved):
        flow_mod.change_hostname_via_webui("LAB-R1", action_id=action_id)
    _stub_subprocess.assert_not_called()


def test_refuses_for_unknown_action_id(_isolated_artifacts):
    with pytest.raises(NotApproved):
        flow_mod.change_hostname_via_webui("LAB-R1", action_id="act_nope")


# ---------------------------------------------------------------------------
# Happy path — full flow with mocked subprocess
# ---------------------------------------------------------------------------


def test_happy_path_returns_structured_result(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    action_id = propose_action("webui_set_hostname", {"new_name": "LAB-R1"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_hostname", return_value=True):
        result = flow_mod.change_hostname_via_webui("LAB-R1", action_id=action_id)

    assert result["tool"] == "webui_set_hostname"
    assert result["new_hostname"] == "LAB-R1"
    assert result["old_hostname"] == "c1111-lab"
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
    """Parent must hand the new_name / action_id / headless flag down to the child."""
    action_id = propose_action("webui_set_hostname", {"new_name": "R1"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_hostname", return_value=True):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id, headless=False)

    _stub_subprocess.assert_called_once()
    flow_name, payload = _stub_subprocess.call_args.args
    assert flow_name == "change_hostname"
    assert payload["new_name"] == "R1"
    assert payload["action_id"] == action_id
    assert payload["headless"] is False


def test_happy_path_takes_pre_then_post_snapshots(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    action_id = propose_action("webui_set_hostname", {"new_name": "R1"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_hostname", return_value=True):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)

    assert _stub_snapshots[0] == (action_id, "pre")
    assert _stub_snapshots[1] == (action_id, "post")


def test_happy_path_invalidates_connection_pool(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    """After WebUI changes hostname, the pooled SSH base_prompt is stale.
    Pool must be invalidated before verification CLI call."""
    action_id = propose_action("webui_set_hostname", {"new_name": "R1"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_hostname", return_value=True):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)

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
    """WebUI clicked Apply but CLI doesn't see the new hostname → raise."""
    from backend.orchestration.confirmations import ActionState, get_action

    action_id = propose_action("webui_set_hostname", {"new_name": "R1"})
    approve_action(action_id)

    with (
        patch.object(flow_mod, "verify_hostname", return_value=False),
        pytest.raises(flow_mod.WebUIVerificationError),
    ):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)

    assert get_action(action_id)["state"] == ActionState.FAILED


def test_subprocess_failure_propagates_and_marks_failed(
    _isolated_artifacts,
    _stub_subprocess,
    _stub_pool,
    _stub_snapshots,
):
    """If the child Playwright process raises (login fail / browser crash /
    selector timeout), the parent must mark_failed and re-raise."""
    from backend.orchestration.confirmations import ActionState, get_action
    from backend.webui_agent._subprocess import SubprocessFlowError

    action_id = propose_action("webui_set_hostname", {"new_name": "R1"})
    approve_action(action_id)

    _stub_subprocess.side_effect = SubprocessFlowError(
        flow="change_hostname",
        error="WebUI login failed",
        exc_type="RuntimeError",
        stderr="(traceback omitted)",
    )

    with pytest.raises(SubprocessFlowError):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)

    assert get_action(action_id)["state"] == ActionState.FAILED
