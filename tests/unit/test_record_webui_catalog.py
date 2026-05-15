"""Unit tests for scripts/record_webui_catalog.py.

Covers the pure-logic helpers (_capture_if_new, _save_catalog) without
starting a real browser or touching the router.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.record_webui_catalog import _capture_if_new, _save_catalog

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_page(url: str = "https://router/foo") -> MagicMock:
    page = MagicMock()
    type(page).url = property(lambda self: url)
    return page


def _fake_view(
    title: str = "Foo",
    elements: list | None = None,
    view_id: str = "v1",
) -> dict:
    return {
        "title": title,
        "view_id": view_id,
        "elements": elements or [],
        "modals": [],
        "errors": [],
    }


def _fake_settings(artifacts_dir: str = "artifacts", router_host: str = "192.168.10.1") -> object:
    return SimpleNamespace(artifacts_dir=artifacts_dir, router_host=router_host)


# ---------------------------------------------------------------------------
# test_capture_if_new_dedupes_by_url
# ---------------------------------------------------------------------------


def test_capture_if_new_dedupes_by_url() -> None:
    """Same URL visited twice must produce only one catalog entry."""
    page = _mock_page("https://router/foo")
    pages: list[dict] = []
    visited: set[str] = set()

    with patch(
        "scripts.record_webui_catalog.describe_page",
        return_value=(_fake_view(), {}),
    ):
        _capture_if_new(page, pages, visited)
        assert len(pages) == 1

        # Second call with the same URL — must be a no-op
        _capture_if_new(page, pages, visited)
        assert len(pages) == 1  # still 1, not 2


def test_capture_if_new_records_entry_fields() -> None:
    """Captured entry must carry the fields describe_page returned."""
    page = _mock_page("https://router/dashboard")
    pages: list[dict] = []
    visited: set[str] = set()

    view = _fake_view(title="Dashboard", elements=[{"eid": "e_001"}], view_id="abc123")
    with patch("scripts.record_webui_catalog.describe_page", return_value=(view, {})):
        _capture_if_new(page, pages, visited)

    assert pages[0]["url"] == "https://router/dashboard"
    assert pages[0]["title"] == "Dashboard"
    assert pages[0]["view_id"] == "abc123"
    assert len(pages[0]["elements"]) == 1
    assert "captured_at" in pages[0]
    assert "describe_failed" not in pages[0]


# ---------------------------------------------------------------------------
# test_capture_if_new_records_stub_on_describe_failure
# ---------------------------------------------------------------------------


def test_capture_if_new_records_stub_on_describe_failure() -> None:
    """When describe_page raises, a stub entry with describe_failed is added."""
    page = _mock_page("https://router/broken")
    pages: list[dict] = []
    visited: set[str] = set()

    with patch(
        "scripts.record_webui_catalog.describe_page",
        side_effect=RuntimeError("boom"),
    ):
        _capture_if_new(page, pages, visited)

    assert len(pages) == 1
    entry = pages[0]
    assert entry["url"] == "https://router/broken"
    assert entry["elements"] == []
    assert "describe_failed" in entry
    assert "boom" in entry["describe_failed"]


def test_capture_if_new_stub_still_dedupes() -> None:
    """Even after a stub is recorded the URL is in visited_urls — no re-capture."""
    page = _mock_page("https://router/broken")
    pages: list[dict] = []
    visited: set[str] = set()

    with patch(
        "scripts.record_webui_catalog.describe_page",
        side_effect=RuntimeError("boom"),
    ):
        _capture_if_new(page, pages, visited)
        _capture_if_new(page, pages, visited)

    assert len(pages) == 1


# ---------------------------------------------------------------------------
# test_save_catalog_writes_both_paths
# ---------------------------------------------------------------------------


def test_save_catalog_writes_both_paths(tmp_path: Path) -> None:
    """_save_catalog must write JSON to both the artifacts and blessed paths."""
    artifacts_dir = tmp_path / "artifacts" / "webui-catalog"
    blessed_dir = tmp_path / "blessed" / "webui-catalog"

    settings = _fake_settings(router_host="192.168.10.1")
    pages = [
        {
            "url": "https://router/foo",
            "title": "Foo",
            "elements": [{"eid": "e_001"}],
            "modals": [],
            "errors": [],
        }
    ]

    with patch("scripts.record_webui_catalog._git_short_sha", return_value="abc1234"):
        rc = _save_catalog(
            pages,
            settings,
            artifacts_dir=artifacts_dir,
            blessed_dir=blessed_dir,
        )

    assert rc == 0

    # Blessed path must exist and be valid JSON
    blessed_path = blessed_dir / "current.json"
    assert blessed_path.exists(), "blessed current.json was not written"
    blessed_data = json.loads(blessed_path.read_text(encoding="utf-8"))
    assert blessed_data["summary"]["total_pages"] == 1
    assert blessed_data["summary"]["total_elements"] == 1
    assert blessed_data["recorder"] == "manual"
    assert blessed_data["git_commit"] == "abc1234"

    # Artifacts dir must contain exactly one timestamped file
    artifact_files = list(artifacts_dir.glob("catalog-*-manual.json"))
    assert len(artifact_files) == 1, f"expected 1 artifact file, got {artifact_files}"
    artifact_data = json.loads(artifact_files[0].read_text(encoding="utf-8"))
    # Both files must carry the same page count
    assert artifact_data["summary"]["total_pages"] == blessed_data["summary"]["total_pages"]


def test_save_catalog_empty_pages(tmp_path: Path) -> None:
    """An empty run (Filip Ctrl+C immediately) must produce valid JSON with 0 pages."""
    artifacts_dir = tmp_path / "a"
    blessed_dir = tmp_path / "b"
    settings = _fake_settings()

    with patch("scripts.record_webui_catalog._git_short_sha", return_value="deadbeef"):
        rc = _save_catalog([], settings, artifacts_dir=artifacts_dir, blessed_dir=blessed_dir)

    assert rc == 0
    blessed_data = json.loads((blessed_dir / "current.json").read_text(encoding="utf-8"))
    assert blessed_data["summary"]["total_pages"] == 0
    assert blessed_data["summary"]["total_elements"] == 0


def test_save_catalog_sums_elements_correctly(tmp_path: Path) -> None:
    """total_elements must be the sum across all pages, not just the page count."""
    artifacts_dir = tmp_path / "a"
    blessed_dir = tmp_path / "b"
    settings = _fake_settings()

    pages = [
        {"url": "u1", "elements": [1, 2, 3]},  # 3 elements
        {"url": "u2", "elements": [1]},  # 1 element
        {"url": "u3", "elements": []},  # 0 elements
    ]
    with patch("scripts.record_webui_catalog._git_short_sha", return_value="x"):
        _save_catalog(pages, settings, artifacts_dir=artifacts_dir, blessed_dir=blessed_dir)

    data = json.loads((blessed_dir / "current.json").read_text(encoding="utf-8"))
    assert data["summary"]["total_pages"] == 3
    assert data["summary"]["total_elements"] == 4
