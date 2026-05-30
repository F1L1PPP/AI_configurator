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


def _make_page(url: str = "http://router/webui/#/dhcp", locator_count: int = 1) -> MagicMock:
    """Return a page mock; locator_count controls page.locator(sel).count() result.

    Default is 1 so that existing cache-hit tests pass (count > 0 → cache valid).
    Pass locator_count=0 to simulate a stale cached selector.
    """
    page = MagicMock()
    page.url = url
    # Configure locator().count() so the pre-trust probe (3.2c) gets a
    # deterministic integer — MagicMock's default comparison is unreliable.
    locator_mock = MagicMock()
    locator_mock.count.return_value = locator_count
    page.locator.return_value = locator_mock
    return page


def _make_ev(tmp_path: Path) -> MagicMock:
    """EvidenceCollector mock whose vision_screenshot writes a real 1-byte PNG."""
    ev = MagicMock()
    ev.vision_call_count = 0
    screenshot_path = tmp_path / "vision-test.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

    def _vision_screenshot(page: object, intent_id: str, viewport_only: bool = False) -> Path:
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
    """Prior PNGs matching the page URL → messages.create includes prior image blocks.

    Chunk 2 (3.4c): reactive path now passes max_n=1, so at most 1 prior
    screenshot is included → total image blocks = 2 (1 current + 1 prior).
    """
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
    # Chunk 2 (3.4c): max_n=1 on the reactive path → 1 current + 1 prior = 2 total.
    assert len(image_blocks) == 2  # 1 current + 1 prior (max_n=1)


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


# ---------------------------------------------------------------------------
# Regression: Anthropic client must receive api_key kwarg
# ---------------------------------------------------------------------------
# Live smoke act_20260523_718d70 fired plan_vision_check_api_error 4x with
# "Could not resolve authentication method". Same construction pattern lives
# here in vision_fallback._call_haiku_vision. Lock the explicit api_key= form.


# ---------------------------------------------------------------------------
# evict_from_selector_cache (chunk 14g)
# ---------------------------------------------------------------------------


def test_evict_from_selector_cache_removes_entry(tmp_path: Path) -> None:
    """Pre-populated entry must be removed on evict."""
    from backend.webui_agent.vision_fallback import (
        _cache_key,
        evict_from_selector_cache,
        load_selector_cache,
        save_selector_cache,
    )

    cache_path = tmp_path / "selector_cache.json"
    page_url = "http://router/webui/#/dhcp"
    key = _cache_key("textbox", "Network", page_url)
    save_selector_cache(cache_path, {key: "input[name='networkIp']"})

    evicted = evict_from_selector_cache(cache_path, "textbox", "Network", page_url)

    assert evicted is True
    assert key not in load_selector_cache(cache_path)


def test_evict_from_selector_cache_missing_key_returns_false(tmp_path: Path) -> None:
    """Evicting a key not in cache must return False without raising."""
    from backend.webui_agent.vision_fallback import evict_from_selector_cache

    cache_path = tmp_path / "selector_cache.json"
    # File doesn't exist; load returns {}.
    evicted = evict_from_selector_cache(cache_path, "textbox", "Network", "http://router/")
    assert evicted is False


def test_anthropic_client_receives_api_key_kwarg(tmp_path: Path) -> None:
    """_call_haiku_vision MUST pass api_key= to Anthropic(), not rely on env."""
    settings = _make_settings(tmp_path)
    settings.anthropic_api_key = "sk-ant-test-fixture"
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    mock_cls = _make_mock_anthropic(_valid_vision_response())

    # get_settings is lazy-imported inside _call_haiku_vision; patch at source.
    with (
        patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        resolve_via_vision(page, intent, ev, settings)

    assert mock_cls.called, "Anthropic() was never called"
    for call in mock_cls.call_args_list:
        assert "api_key" in call.kwargs, (
            f"Anthropic constructed without api_key kwarg: {call}. "
            f"See live smoke act_20260523_718d70 for the regression."
        )
        assert call.kwargs["api_key"] == "sk-ant-test-fixture"


# ---------------------------------------------------------------------------
# Regression: empty / prose-around-JSON responses must not crash
# ---------------------------------------------------------------------------
# Live smoke act_20260523_6dc28c (visible for the first time after 14h-C
# subprocess log forwarding) showed every vision_fallback call returning
# JSONDecodeError "Expecting value: line 1 column 1 (char 0)" — Haiku's
# response.content[0].text was empty/prose. Without recovery, every
# selector resolution silently fails → falls through to heuristics →
# picks wrong element (the e_013 link bug we've chased all day).
# Mirror of plan_vision_check fix from commit 27a0421.


def test_resolve_via_vision_empty_response_returns_none_gracefully(tmp_path: Path) -> None:
    """Empty content[0].text → ValueError raised internally → caught by outer
    except → resolve_via_vision returns None (no crash, no JSONDecodeError leak).
    This is the live-smoke act_20260523_6dc28c shape."""
    settings = _make_settings(tmp_path)
    settings.anthropic_api_key = "sk-ant-test"
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    mock_cls = _make_mock_anthropic("")  # empty content

    with (
        patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result is None
    # Cap should NOT have been bumped — the call raised before increment.
    assert ev.vision_call_count == 0


def test_resolve_via_vision_prose_around_json_is_recovered(tmp_path: Path) -> None:
    """Haiku returns prose + valid JSON object → brace extraction recovers it."""
    settings = _make_settings(tmp_path)
    settings.anthropic_api_key = "sk-ant-test"
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    prose_response = (
        "Looking at the screenshot, the Network input field has name='networkIp'. "
        '{"selector": "input[name=\\"networkIp\\"]", "confidence": 0.92, '
        '"reasoning": "matched by HTML name attribute"}'
    )
    mock_cls = _make_mock_anthropic(prose_response)

    with (
        patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result == 'input[name="networkIp"]'
    # Cache populated with the recovered selector.
    saved = load_selector_cache(settings.selector_cache_path)
    key = _cache_key("textbox", "Network", page.url)
    assert saved.get(key) == 'input[name="networkIp"]'


def test_resolve_via_vision_prose_only_no_json_returns_none(tmp_path: Path) -> None:
    """Haiku returns prose with NO JSON object → outer except catches → None."""
    settings = _make_settings(tmp_path)
    settings.anthropic_api_key = "sk-ant-test"
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    mock_cls = _make_mock_anthropic("I cannot identify the element with confidence.")

    with (
        patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        result = resolve_via_vision(page, intent, ev, settings)

    assert result is None


def test_extract_first_json_object_handles_nested_braces() -> None:
    """Brace counter must correctly handle nested {} inside the JSON value."""
    from backend.webui_agent.vision_fallback import _extract_first_json_object

    text = 'prose {"a": {"nested": "x"}, "b": [1,2]} trailing'
    assert _extract_first_json_object(text) == '{"a": {"nested": "x"}, "b": [1,2]}'


def test_extract_first_json_object_ignores_braces_in_strings() -> None:
    """Braces inside double-quoted strings must not affect depth counting."""
    from backend.webui_agent.vision_fallback import _extract_first_json_object

    text = '{"selector": "div[data-foo=\\"{not-a-brace}\\"]", "confidence": 0.9}'
    extracted = _extract_first_json_object(text)
    assert extracted == text
    parsed = json.loads(extracted)
    assert parsed["confidence"] == 0.9


def test_extract_first_json_object_returns_none_when_no_object() -> None:
    from backend.webui_agent.vision_fallback import _extract_first_json_object

    assert _extract_first_json_object("just prose with no braces") is None
    assert _extract_first_json_object("") is None
    assert _extract_first_json_object("{ unterminated") is None


# ---------------------------------------------------------------------------
# Regression: vision prompt must demand UNIQUE selectors
# ---------------------------------------------------------------------------
# Live smoke act_20260523_90c146 showed Haiku returning bare
# `button:has-text('Add')` which matched multiple Add buttons on the DHCP
# page → click hung → 30s timeout → session_not_found cascade. Prompt now
# explicitly forbids bare role+text selectors and demands one of: HTML
# attribute, aria-label, container-scoped, or :nth-match. Lock the words
# so a future prompt edit doesn't silently drop the guidance.


def test_vision_prompt_demands_unique_selectors(tmp_path: Path) -> None:
    """The Haiku vision prompt must contain selector-uniqueness clauses."""
    settings = _make_settings(tmp_path)
    settings.anthropic_api_key = "sk-ant-test"
    page = _make_page()
    ev = _make_ev(tmp_path)
    intent = {"role": "button", "name": "Add", "action": "click", "value": None}

    mock_cls = _make_mock_anthropic(
        json.dumps({"selector": "[aria-label='Add Pool']", "confidence": 0.95})
    )

    with (
        patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        resolve_via_vision(page, intent, ev, settings)

    # Grab the actual messages payload sent to Haiku.
    call_kwargs = mock_cls.return_value.messages.create.call_args.kwargs
    content_blocks = call_kwargs["messages"][0]["content"]
    prompt_text = " ".join(b["text"] for b in content_blocks if b.get("type") == "text")

    # Must demand uniqueness explicitly.
    assert "EXACTLY ONE" in prompt_text or "exactly one" in prompt_text.lower(), (
        "Vision prompt no longer demands unique selectors — regression of 14h-D fix"
    )
    # Must call out the live-smoke bad pattern as forbidden.
    assert "button:has-text" in prompt_text or "FORBIDDEN" in prompt_text, (
        "Vision prompt no longer forbids bare role+text selectors"
    )
    # Must prefer attribute-based selectors.
    assert "aria-label" in prompt_text, (
        "Vision prompt no longer prefers aria-label / attribute selectors"
    )


# ---------------------------------------------------------------------------
# Chunk 2 — 3.2a: load_selector_cache drops malformed entries
# ---------------------------------------------------------------------------


def test_load_selector_cache_drops_malformed_entries(tmp_path: Path) -> None:
    """load_selector_cache must silently drop entries with empty name, non-str
    selector, or wrong key format, while keeping valid entries intact.

    The live poison entry 'textbox||c737961f1b1a' (empty name → 2nd part is
    empty) triggered wrong-page cache hits on the DHCP form.
    """
    cache_path = tmp_path / "selector_cache.json"
    raw = {
        # VALID: role|name|hash — all 3 parts non-empty, string selector.
        "textbox|Network|abc123456789": "input[name='networkIp']",
        # INVALID: empty name (the live poison entry shape).
        "textbox||c737961f1b1a": "input[name='something']",
        # INVALID: non-string selector.
        "button|Add|def456789012": 42,
        # INVALID: only 2 parts.
        "button|Apply": "button.apply",
        # INVALID: 4 parts.
        "button|Apply|hash1|extra": "button.apply",
        # INVALID: empty selector string.
        "select|Proto|aaa111222333": "",
    }
    cache_path.write_text(json.dumps(raw), encoding="utf-8")

    result = load_selector_cache(cache_path)

    # Only the valid entry survives.
    assert len(result) == 1
    assert "textbox|Network|abc123456789" in result
    assert result["textbox|Network|abc123456789"] == "input[name='networkIp']"

    # The poison entry is gone.
    assert "textbox||c737961f1b1a" not in result


def test_load_selector_cache_non_dict_json_returns_empty(tmp_path: Path) -> None:
    """Valid JSON that isn't an object (list/scalar) is malformed → return {}.

    Guards the load_selector_cache / resolve_via_vision "never raises" contract
    against an AttributeError on .items().
    """
    cache_path = tmp_path / "selector_cache.json"

    cache_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_selector_cache(cache_path) == {}

    cache_path.write_text('"just a string"', encoding="utf-8")
    assert load_selector_cache(cache_path) == {}


# ---------------------------------------------------------------------------
# Chunk 2 — 3.2b: _hash_page_url distinguishes fragments
# ---------------------------------------------------------------------------


def test_hash_page_url_distinguishes_fragments() -> None:
    """Fragment-differing SPA URLs must produce distinct hashes.

    Pre-chunk-2: fragment was stripped → #/dhcp and #/ospf collided.
    """
    from backend.webui_agent.vision_fallback import _hash_page_url

    hash_dhcp = _hash_page_url("http://router/webui/#/dhcp")
    hash_ospf = _hash_page_url("http://router/webui/#/ospf")

    assert hash_dhcp != hash_ospf, (
        "URLs with different fragments must produce different hashes. "
        "Pre-chunk-2 bug: both were mapped to the same hash (fragment was stripped)."
    )


def test_hash_page_url_strips_query_params() -> None:
    """Query params must still be stripped (unchanged behaviour)."""
    from backend.webui_agent.vision_fallback import _hash_page_url

    h1 = _hash_page_url("http://router/webui/?ts=1")
    h2 = _hash_page_url("http://router/webui/?ts=9999")
    assert h1 == h2


def test_hash_page_url_includes_fragment_in_hash() -> None:
    """Same path, same fragment → same hash; different fragment → different hash."""
    from backend.webui_agent.vision_fallback import _hash_page_url

    h_no_frag = _hash_page_url("http://router/webui/")
    h_with_frag = _hash_page_url("http://router/webui/#/dhcp")

    assert h_no_frag != h_with_frag


# ---------------------------------------------------------------------------
# Chunk 2 — 3.2c: pre-trust probe on cache hit
# ---------------------------------------------------------------------------


def test_cache_hit_valid_selector_returns_without_anthropic(tmp_path: Path) -> None:
    """Cache hit where locator.count() > 0 → return cached selector; no Haiku call."""
    settings = _make_settings(tmp_path)
    # page.locator(sel).count() returns 1 → selector is live.
    page = _make_page(locator_count=1)
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    key = _cache_key("textbox", "Network", page.url)
    cached_selector = 'input[aria-label="Network"]'
    save_selector_cache(settings.selector_cache_path, {key: cached_selector})

    with patch("backend.webui_agent.vision_fallback.Anthropic") as mock_cls:
        result = resolve_via_vision(page, intent, ev, settings)

    assert result == cached_selector
    mock_cls.assert_not_called()


def test_cache_hit_stale_selector_evicted_and_reresolved(tmp_path: Path) -> None:
    """Cache hit where locator.count() == 0 → evict stale entry + fall through to Haiku."""
    settings = _make_settings(tmp_path)
    # page.locator(sel).count() returns 0 → selector is stale.
    page = _make_page(locator_count=0)
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "20.20.20.0"}

    key = _cache_key("textbox", "Network", page.url)
    stale_selector = 'input[aria-label="StaleNetwork"]'
    save_selector_cache(settings.selector_cache_path, {key: stale_selector})

    fresh_selector = 'input[name="networkIp"]'
    mock_cls = _make_mock_anthropic(
        json.dumps({"selector": fresh_selector, "confidence": 0.92, "reasoning": "re-resolved"})
    )

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    # The stale entry was evicted and Haiku returned a fresh selector.
    assert result == fresh_selector
    mock_cls.return_value.messages.create.assert_called_once()

    # The cache now contains the fresh selector, not the stale one.
    saved = load_selector_cache(settings.selector_cache_path)
    assert saved.get(key) == fresh_selector
    assert stale_selector not in saved.values()


def test_cache_hit_exception_in_locator_count_treated_as_stale(tmp_path: Path) -> None:
    """If page.locator(sel).count() raises, treat as stale (count=0) and re-resolve."""
    settings = _make_settings(tmp_path)
    page = _make_page()
    page.locator.return_value.count.side_effect = RuntimeError("page crashed")
    ev = _make_ev(tmp_path)
    intent = {"role": "textbox", "name": "Network", "action": "fill", "value": "x"}

    key = _cache_key("textbox", "Network", page.url)
    save_selector_cache(settings.selector_cache_path, {key: "input[stale]"})

    fresh_selector = 'input[name="networkIp"]'
    mock_cls = _make_mock_anthropic(
        json.dumps({"selector": fresh_selector, "confidence": 0.9, "reasoning": "ok"})
    )

    with patch("backend.webui_agent.vision_fallback.Anthropic", mock_cls):
        result = resolve_via_vision(page, intent, ev, settings)

    # Exception treated as stale → Haiku re-resolves.
    assert result == fresh_selector
    mock_cls.return_value.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# Chunk 2 — 3.4a: viewport_only screenshot param
# ---------------------------------------------------------------------------


def test_vision_screenshot_viewport_only_param(tmp_path: Path) -> None:
    """vision_screenshot with viewport_only=True must call page.screenshot(full_page=False)."""
    import backend.webui_agent.evidence as ev_mod

    fake_settings = MagicMock()
    fake_settings.artifacts_dir = tmp_path

    with patch.object(ev_mod, "get_settings", return_value=fake_settings):
        from backend.webui_agent.evidence import EvidenceCollector

        ec = EvidenceCollector("test_flow", action_id="act_vp")

    page = MagicMock()

    def fake_screenshot(path: str, full_page: bool = True) -> None:
        Path(path).write_bytes(b"png")

    page.screenshot.side_effect = fake_screenshot

    # viewport_only=True → full_page=False
    ec.vision_screenshot(page, "abc123", viewport_only=True)
    call_kwargs = page.screenshot.call_args.kwargs
    assert call_kwargs.get("full_page") is False, (
        "viewport_only=True must call screenshot(full_page=False)"
    )

    page.screenshot.reset_mock()

    # Default (viewport_only=False) → full_page=True
    ec.vision_screenshot(page, "def456")
    call_kwargs = page.screenshot.call_args.kwargs
    assert call_kwargs.get("full_page") is True, (
        "Default viewport_only=False must call screenshot(full_page=True)"
    )
