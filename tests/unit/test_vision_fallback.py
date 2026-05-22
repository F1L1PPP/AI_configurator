"""Unit tests for backend.webui_agent.vision_fallback (chunk 14b).

All Anthropic calls are mocked — no network, no real images.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.webui_agent.vision_fallback import (
    _MODEL,
    _cache_key,
    _find_prior_screenshots,
    _latest_post_running_config,
    load_selector_cache,
    resolve_via_vision,
    save_selector_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.artifacts_dir = tmp_path / "artifacts"
    settings.selector_cache_path = tmp_path / "artifacts" / "selector_cache.json"
    return settings


def _make_page(url: str = "http://router/webui/#/dhcp") -> MagicMock:
    page = MagicMock()
    page.url = url
    return page


def _make_ev(tmp_path: Path) -> MagicMock:
    """EvidenceCollector mock whose vision_screenshot writes a real 1-byte PNG."""
    ev = MagicMock()
    ev.vision_call_count = 0
    screenshot_path = tmp_path / "vision-test.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

    def _vision_screenshot(page: object, intent_id: str) -> Path:
        return screenshot_path

    ev.vision_screenshot.side_effect = _vision_screenshot
    return ev


def _make_mock_anthropic(text: str) -> MagicMock:
    """Patch target: ``backend.webui_agent.vision_fallback.Anthropic``."""
    mock_client_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=text)]
    mock_client_instance.messages.create.return_value = mock_response

    mock_anthropic_cls = MagicMock(return_value=mock_client_instance)
    return mock_anthropic_cls


def _valid_vision_response(
    selector: str = 'input[aria-label="Network"]', confidence: float = 0.9
) -> str:
    return json.dumps(
        {"selector": selector, "confidence": confidence, "reasoning": "visible label"}
    )


# ---------------------------------------------------------------------------
# resolve_via_vision — cache behaviour
# ---------------------------------------------------------------------------


def test_resolve_via_vision_cache_hit_skips_api(tmp_path: Path) -> None:
    """Pre-populated cache entry must be returned without calling Anthropic."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    # Pre-populate the cache.
    key = _cache_key("textbox", "Network", page.url)
    cached_selector = 'input[aria-label="Network"]'
    save_selector_cache(settings.selector_cache_path, {key: cached_selector})

    with patch("backend.webui_agent.vision_fallback.Anthropic") as mock_cls:
        result = resolve_via_vision(page, intent, ev, settings)

    assert result == cached_selector
    mock_cls.assert_not_called()


def test_resolve_via_vision_cache_miss_calls_anthropic(tmp_path: Path) -> None:
    """Empty cache must trigger an Anthropic vision call."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    mock_cls = _make_mock_anthropic(_valid_vision_response())

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result == 'input[aria-label="Network"]'
    mock_cls.return_value.messages.create.assert_called_once()


def test_resolve_via_vision_saves_on_success(tmp_path: Path) -> None:
    """A successful vision resolution must persist the selector to cache."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    mock_cls = _make_mock_anthropic(_valid_vision_response())

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        resolve_via_vision(page, intent, ev, settings)

    saved = load_selector_cache(settings.selector_cache_path)
    key = _cache_key("textbox", "Network", page.url)
    assert key in saved
    assert saved[key] == 'input[aria-label="Network"]'


def test_resolve_via_vision_low_confidence_returns_none(tmp_path: Path) -> None:
    """Confidence below 0.7 must return None and leave cache empty."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    mock_cls = _make_mock_anthropic(_valid_vision_response(confidence=0.5))

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result is None
    saved = load_selector_cache(settings.selector_cache_path)
    assert saved == {}


def test_resolve_via_vision_api_error_returns_none(tmp_path: Path) -> None:
    """Any Anthropic API exception must return None (never raise)."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API unavailable")
    mock_cls = MagicMock(return_value=mock_client)

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result is None


def test_resolve_via_vision_malformed_json_returns_none(tmp_path: Path) -> None:
    """Non-JSON response from Haiku must return None."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    mock_cls = _make_mock_anthropic("Sorry, I cannot identify the element.")

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result is None


# ---------------------------------------------------------------------------
# resolve_via_vision — grounding context (prior screenshots + running-config)
# ---------------------------------------------------------------------------


def test_resolve_via_vision_with_prior_screenshots(tmp_path: Path) -> None:
    """2 prior PNGs matching the page URL → messages.create called with 3 image blocks."""
    settings = _make_settings(tmp_path)
    page = _make_page(url="http://router/webui/#/dhcp")
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    # Create 2 PNG files in a screenshots subdir whose path contains "dhcp".
    dhcp_dir = settings.artifacts_dir / "screenshots" / "dhcp_flow_abc" / "01"
    dhcp_dir.mkdir(parents=True)
    for i in range(2):
        (dhcp_dir / f"0{i + 1}-step.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]))

    mock_cls = _make_mock_anthropic(_valid_vision_response())

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        resolve_via_vision(page, intent, ev, settings)

    call_kwargs = mock_cls.return_value.messages.create.call_args
    content = call_kwargs.kwargs["messages"][0]["content"]

    # Count image blocks.
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 3  # 1 current + 2 priors


def test_resolve_via_vision_with_zero_priors(tmp_path: Path) -> None:
    """Empty screenshots_dir → 1 image block only, no crash."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    # Don't create any screenshots.
    mock_cls = _make_mock_anthropic(_valid_vision_response())

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result is not None
    content = mock_cls.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1


def test_resolve_via_vision_with_running_config(tmp_path: Path) -> None:
    """Existing running-config snapshot → text block included in API call."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    snap_dir = settings.artifacts_dir / "device-snapshots" / "act_001" / "post"
    snap_dir.mkdir(parents=True)
    config_content = "ip dhcp pool MYPOOL\n network 20.20.20.0 255.255.255.0\n"
    (snap_dir / "show_running-config.txt").write_text(config_content, encoding="utf-8")

    mock_cls = _make_mock_anthropic(_valid_vision_response())

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        resolve_via_vision(page, intent, ev, settings)

    content = mock_cls.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
    text_blocks = [b for b in content if b.get("type") == "text"]
    combined = "\n".join(b["text"] for b in text_blocks)
    assert "running-config" in combined
    assert "20.20.20.0" in combined


def test_resolve_via_vision_no_running_config_omits_block(tmp_path: Path) -> None:
    """No snapshot directory → running-config text block absent from API call."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    mock_cls = _make_mock_anthropic(_valid_vision_response())

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        resolve_via_vision(page, intent, ev, settings)

    content = mock_cls.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
    text_blocks = [b for b in content if b.get("type") == "text"]
    combined = "\n".join(b["text"] for b in text_blocks)
    assert "running-config" not in combined


# ---------------------------------------------------------------------------
# _find_prior_screenshots
# ---------------------------------------------------------------------------


def test_find_prior_screenshots_filters_by_url_and_caps(tmp_path: Path) -> None:
    """3 matching + 2 non-matching PNGs → returns 2 most-recent matching."""
    screenshots_dir = tmp_path / "screenshots"
    matching_dir = screenshots_dir / "dhcp_flow_abc"
    matching_dir.mkdir(parents=True)
    non_matching_dir = screenshots_dir / "ospf_flow_xyz"
    non_matching_dir.mkdir(parents=True)

    # Create 3 matching files with distinct mtimes.
    for i in range(3):
        p = matching_dir / f"0{i + 1}-step.png"
        p.write_bytes(b"\x89PNG" + bytes([i]))
        # Spread mtimes so sort order is deterministic.
        mtime = time.time() - (3 - i) * 10
        os.utime(p, (mtime, mtime))

    # 2 non-matching.
    for i in range(2):
        (non_matching_dir / f"0{i + 1}-step.png").write_bytes(b"\x89PNG" + bytes([i + 10]))

    results = _find_prior_screenshots(screenshots_dir, "http://router/webui/#/dhcp", max_n=2)

    assert len(results) == 2
    # Most recent first — the file with index 2 (mtime - 10s) is most recent.
    for p in results:
        assert "dhcp" in str(p).lower()


# ---------------------------------------------------------------------------
# _latest_post_running_config
# ---------------------------------------------------------------------------


def test_latest_post_running_config_truncates_oversized(tmp_path: Path) -> None:
    """Content > 8192 bytes must be truncated with the standard marker."""
    snapshots_dir = tmp_path / "snapshots"
    post_dir = snapshots_dir / "act_001" / "post"
    post_dir.mkdir(parents=True)

    big_content = "x" * 10_000
    (post_dir / "show_running-config.txt").write_text(big_content, encoding="utf-8")

    result = _latest_post_running_config(snapshots_dir)

    assert result is not None
    assert "truncated at 8192 bytes" in result
    assert len(result) <= 8192 + 40  # small slack for the marker line


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------


def test_cache_key_stable_across_url_variants() -> None:
    """Query string differences must not change the cache key."""
    key1 = _cache_key("button", "Add", "http://router/?ts=1")
    key2 = _cache_key("button", "Add", "http://router/?ts=2")
    assert key1 == key2


def test_cache_key_differs_for_different_roles() -> None:
    key1 = _cache_key("button", "Add", "http://router/")
    key2 = _cache_key("textbox", "Add", "http://router/")
    assert key1 != key2


# ---------------------------------------------------------------------------
# _MODEL constant
# ---------------------------------------------------------------------------


def test_model_constant_is_haiku_45() -> None:
    assert _MODEL == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Per-session cost cap
# ---------------------------------------------------------------------------


def test_resolve_via_vision_respects_session_cap(tmp_path: Path) -> None:
    """Once vision_call_count reaches _MAX_VISION_CALLS_PER_SESSION, return None without Anthropic call."""
    from backend.webui_agent.vision_fallback import _MAX_VISION_CALLS_PER_SESSION

    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    ev.vision_call_count = _MAX_VISION_CALLS_PER_SESSION  # already at cap
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    mock_cls = _make_mock_anthropic(_valid_vision_response())

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result is None
    mock_cls.assert_not_called()  # cap hit before API call


def test_resolve_via_vision_cache_hit_does_not_consume_budget(tmp_path: Path) -> None:
    """Cache-hit calls must not increment vision_call_count."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    ev = _make_ev(tmp_path)
    ev.vision_call_count = 0
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    key = _cache_key("textbox", "Network", page.url)
    save_selector_cache(settings.selector_cache_path, {key: 'input[aria-label="Network"]'})

    with patch("backend.webui_agent.vision_fallback.Anthropic"):
        resolve_via_vision(page, intent, ev, settings)

    assert ev.vision_call_count == 0  # cache hit was free
