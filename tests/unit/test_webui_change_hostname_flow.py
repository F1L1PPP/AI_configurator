"""Unit tests for backend.webui_agent.flows.change_hostname — mocked Playwright + CLI."""

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

# All tests in this module exercise the WebUI agent layer (Playwright is
# mocked at the page-object level so no real browser launches). Tagged with
# the `webui` marker so `pytest -m 'not webui'` skips them during fast
# iteration on unrelated layers. Review §5 cleanup.
pytestmark = pytest.mark.webui

# _clean_actions fixture is now in tests/conftest.py (autouse).


@pytest.fixture()
def _isolated_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import backend.webui_agent.evidence as ev_mod

    fake = MagicMock()
    fake.artifacts_dir = tmp_path
    fake.router_host = "10.0.0.1"
    fake.router_ssh_user = "admin"
    fake.router_ssh_password = "pass"
    monkeypatch.setattr(ev_mod, "get_settings", lambda: fake)
    monkeypatch.setattr(flow_mod, "get_settings", lambda: fake)
    return tmp_path


@pytest.fixture()
def _stub_browser(monkeypatch: pytest.MonkeyPatch):
    """Replace webui_browser context manager with one yielding a mock Page."""
    from contextlib import contextmanager

    page = MagicMock()
    page.screenshot = MagicMock(
        side_effect=lambda path, full_page=False: Path(path).write_bytes(b"png")
    )
    page.content.return_value = "<html></html>"

    @contextmanager
    def fake_browser(headless: bool = False):
        yield page

    monkeypatch.setattr(flow_mod, "webui_browser", fake_browser)
    return page


@pytest.fixture()
def _stub_login(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(flow_mod, "login", MagicMock(return_value=True))


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
def _stub_hostname_page(monkeypatch: pytest.MonkeyPatch):
    """Replace HostnamePage with a mock class."""
    instance = MagicMock()
    instance.get_current_hostname.return_value = "c1111-lab"

    class FakeHP:
        def __init__(self, page):
            pass

        def __getattr__(self, name):
            return getattr(instance, name)

    monkeypatch.setattr(flow_mod, "HostnamePage", FakeHP)
    return instance


# ---------------------------------------------------------------------------
# Approval gate — refuse without APPROVED action
# ---------------------------------------------------------------------------


def test_refuses_without_approval(
    _isolated_artifacts, _stub_browser, _stub_login, _stub_hostname_page
):
    action_id = propose_action("webui_set_hostname", {"name": "LAB-R1"})
    # NOT approved
    with pytest.raises(NotApproved):
        flow_mod.change_hostname_via_webui("LAB-R1", action_id=action_id)


def test_refuses_for_unknown_action_id(_isolated_artifacts):
    with pytest.raises(NotApproved):
        flow_mod.change_hostname_via_webui("LAB-R1", action_id="act_nope")


# ---------------------------------------------------------------------------
# Happy path — full flow with mocked browser, login, page, verify
# ---------------------------------------------------------------------------


def test_happy_path_returns_structured_result(
    _isolated_artifacts,
    _stub_browser,
    _stub_login,
    _stub_pool,
    _stub_snapshots,
    _stub_hostname_page,
):
    action_id = propose_action("webui_set_hostname", {"name": "LAB-R1"})
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


def test_happy_path_takes_pre_then_post_snapshots(
    _isolated_artifacts,
    _stub_browser,
    _stub_login,
    _stub_pool,
    _stub_snapshots,
    _stub_hostname_page,
):
    action_id = propose_action("webui_set_hostname", {"name": "R1"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_hostname", return_value=True):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)

    assert _stub_snapshots[0] == (action_id, "pre")
    assert _stub_snapshots[1] == (action_id, "post")


def test_happy_path_invalidates_connection_pool(
    _isolated_artifacts,
    _stub_browser,
    _stub_login,
    _stub_pool,
    _stub_snapshots,
    _stub_hostname_page,
):
    """After WebUI changes hostname, the pooled SSH base_prompt is stale.
    Pool must be invalidated before verification CLI call."""
    action_id = propose_action("webui_set_hostname", {"name": "R1"})
    approve_action(action_id)

    with patch.object(flow_mod, "verify_hostname", return_value=True):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)

    _stub_pool.invalidate.assert_called_once()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_verification_failure_raises_and_marks_failed(
    _isolated_artifacts,
    _stub_browser,
    _stub_login,
    _stub_pool,
    _stub_snapshots,
    _stub_hostname_page,
):
    """WebUI clicked Apply but CLI doesn't see the new hostname → raise."""
    from backend.orchestration.confirmations import get_action

    action_id = propose_action("webui_set_hostname", {"name": "R1"})
    approve_action(action_id)

    with (
        patch.object(flow_mod, "verify_hostname", return_value=False),
        pytest.raises(flow_mod.WebUIVerificationError),
    ):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)

    # action_id must be marked FAILED
    from backend.orchestration.confirmations import ActionState

    assert get_action(action_id)["state"] == ActionState.FAILED


def test_login_failure_aborts_flow(
    _isolated_artifacts, _stub_browser, _stub_pool, _stub_snapshots, _stub_hostname_page
):
    action_id = propose_action("webui_set_hostname", {"name": "R1"})
    approve_action(action_id)

    with (
        patch.object(flow_mod, "login", return_value=False),
        pytest.raises(RuntimeError, match="login failed"),
    ):
        flow_mod.change_hostname_via_webui("R1", action_id=action_id)
