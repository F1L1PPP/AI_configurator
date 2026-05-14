"""Unit tests for backend.webui_agent.evidence — screenshot + DOM helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# All tests in this module exercise the WebUI agent layer (Playwright is
# mocked at the page-object level so no real browser launches). Tagged with
# the `webui` marker so `pytest -m 'not webui'` skips them during fast
# iteration on unrelated layers. Review §5 cleanup.
pytestmark = pytest.mark.webui


@pytest.fixture()
def _isolated_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point get_settings().artifacts_dir at a temp directory for the test."""
    import backend.webui_agent.evidence as ev_mod

    fake = MagicMock()
    fake.artifacts_dir = tmp_path
    monkeypatch.setattr(ev_mod, "get_settings", lambda: fake)
    return tmp_path


def _fake_page() -> MagicMock:
    page = MagicMock()

    def fake_screenshot(path: str, full_page: bool = False):
        # Create the file so step() return value is verifiable
        Path(path).write_bytes(b"png")

    page.screenshot.side_effect = fake_screenshot
    page.content.return_value = "<html><body>fake</body></html>"
    return page


# ---------------------------------------------------------------------------
# Session directory layout
# ---------------------------------------------------------------------------


def test_session_dir_uses_action_id_when_provided(_isolated_artifacts: Path):
    from backend.webui_agent.evidence import EvidenceCollector

    ev = EvidenceCollector("change_hostname", action_id="act_20260512_abc")
    assert ev.session_dir.exists()
    assert ev.session_dir.parent == _isolated_artifacts / "screenshots"
    assert "act_20260512_abc" in ev.session_dir.name
    assert "change_hostname" in ev.session_dir.name


def test_session_dir_falls_back_to_timestamp_when_no_action_id(
    _isolated_artifacts: Path,
):
    from backend.webui_agent.evidence import EvidenceCollector

    ev = EvidenceCollector("add_vlan")
    assert ev.session_dir.exists()
    # Without action_id, the name should still be unique-per-second
    assert "add_vlan" in ev.session_dir.name


# ---------------------------------------------------------------------------
# step() — auto-numbered screenshots
# ---------------------------------------------------------------------------


def test_step_creates_numbered_screenshot(_isolated_artifacts: Path):
    from backend.webui_agent.evidence import EvidenceCollector

    ev = EvidenceCollector("change_hostname", action_id="act_x")
    page = _fake_page()
    path = ev.step("01-login", page)
    assert path.exists()
    assert path.name == "01-01-login.png"
    page.screenshot.assert_called_once()


def test_step_count_increments(_isolated_artifacts: Path):
    from backend.webui_agent.evidence import EvidenceCollector

    ev = EvidenceCollector("flow", action_id="act_y")
    page = _fake_page()
    p1 = ev.step("first", page)
    p2 = ev.step("second", page)
    p3 = ev.step("third", page)
    assert p1.name.startswith("01-")
    assert p2.name.startswith("02-")
    assert p3.name.startswith("03-")
    assert ev.step_count == 3


# ---------------------------------------------------------------------------
# dump_dom() — saves HTML on demand
# ---------------------------------------------------------------------------


def test_dump_dom_writes_html(_isolated_artifacts: Path):
    from backend.webui_agent.evidence import EvidenceCollector

    ev = EvidenceCollector("flow", action_id="act_z")
    page = _fake_page()
    path = ev.dump_dom(page, label="99-exception")
    assert path.exists()
    assert path.suffix == ".html"
    assert "fake" in path.read_text(encoding="utf-8")


def test_dump_dom_default_label(_isolated_artifacts: Path):
    from backend.webui_agent.evidence import EvidenceCollector

    ev = EvidenceCollector("flow", action_id="act_w")
    page = _fake_page()
    path = ev.dump_dom(page)
    assert path.name == "dom.html"
