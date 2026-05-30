"""Unit tests for backend.orchestration.plan_vision_check (chunk 14f-adaptive).

All Anthropic calls are mocked — no network, no real images.
18 tests covering: familiarity formula, tier logic, budget cap, kill switch,
cache atomicity, DHCP smoke regression, and intent canonicalization.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.orchestration.plan_vision_check import (
    _MODEL,
    _intent_key,
    _plan_sha1,
    _plan_validation_signal,
    check_plan_via_vision,
    compute_familiarity_score,
    load_plan_validation_cache,
    record_plan_success,
    save_plan_validation_cache,
)
from backend.webui_agent.vision_fallback import _cache_key, save_selector_cache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.artifacts_dir = tmp_path / "artifacts"
    settings.selector_cache_path = tmp_path / "artifacts" / "selector_cache.json"
    settings.plan_validation_cache_path = tmp_path / "artifacts" / "plan_validation_cache.json"
    settings.logs_dir = tmp_path / "logs"
    settings.plan_vision_enabled = True
    return settings


def _make_ev() -> MagicMock:
    ev = MagicMock()
    ev.plan_vision_count = 0
    return ev


def _make_mock_anthropic(text: str) -> MagicMock:
    """Patch target: backend.orchestration.plan_vision_check.Anthropic."""
    mock_client_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=text)]
    mock_client_instance.messages.create.return_value = mock_response

    mock_anthropic_cls = MagicMock(return_value=mock_client_instance)
    return mock_anthropic_cls


def _make_dhcp_bad_plan() -> list[dict]:
    """The known-bad plan from the live DHCP smoke failure (act_20260523_484286).

    Inner Haiku skipped the Network field in iter 2, then put 255.255.255.0
    (subnet mask) into the Starting IP field. This is the ground-truth regression.
    """
    return [
        {
            "intent": {"role": "textbox", "name": "Starting IP Address"},
            "action": "fill",
            "value": "255.255.255.0",  # WRONG — subnet mask in IP field
        },
        {
            "intent": {"role": "textbox", "name": "DNS Server"},
            "action": "fill",
            "value": "8.8.8.8",
        },
    ]
    # Missing: Network field (20.20.20.0), Prefix field (255.255.255.0 in correct field)


_MINIMAL_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
_DHCP_INTENT = "Configure DHCP pool MYPOOL on interface GigabitEthernet0/0/0"


# ---------------------------------------------------------------------------
# Test 1: fresh state → familiarity 0
# ---------------------------------------------------------------------------


def test_familiarity_score_zero_for_unknown_page(tmp_path: Path) -> None:
    """Fresh tmp_path, empty caches, no logs → score is 0.0."""
    settings = _make_settings(tmp_path)
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]

    score = compute_familiarity_score(
        page_url="http://router/webui/#/dhcp",
        intent=_DHCP_INTENT,
        plan=plan,
        settings=settings,
    )

    # No signals populated → raw = 0.
    assert score == 0.0


# ---------------------------------------------------------------------------
# Test 2: populated selector_cache → cache_hit_signal reflected
# ---------------------------------------------------------------------------


def test_familiarity_score_high_with_cache_hits(tmp_path: Path) -> None:
    """All plan steps in selector_cache → cache_hit_signal = 1.0 → score ≥ 0.40."""
    settings = _make_settings(tmp_path)
    page_url = "http://router/webui/#/dhcp"
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"},
        {
            "intent": {"role": "textbox", "name": "Starting IP Address"},
            "action": "fill",
            "value": "10.0.0.1",
        },
    ]

    # Pre-populate selector_cache with matching entries.
    cache: dict[str, str] = {}
    for step in plan:
        key = _cache_key(step["intent"]["role"], step["intent"]["name"], page_url)
        cache[key] = f'input[aria-label="{step["intent"]["name"]}"]'
    save_selector_cache(settings.selector_cache_path, cache)

    score = compute_familiarity_score(
        page_url=page_url,
        intent=_DHCP_INTENT,
        plan=plan,
        settings=settings,
    )

    # cache_hit_signal = 1.0 → 0.40 * 1.0 = 0.40 contribution minimum.
    assert score >= 0.40


# ---------------------------------------------------------------------------
# Test 3: failure events only → success_signal stays low (gaming defense)
# ---------------------------------------------------------------------------


def test_familiarity_score_filters_failed_actions(tmp_path: Path) -> None:
    """REGRESSION GUARD: 10 failure events + 0 success events → success_signal ≈ 0."""
    settings = _make_settings(tmp_path)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / "actions.log"

    # Write 10 failure-only events.
    events = [
        json.dumps({"event": "webui_act_by_intent_soft_failure", "action_id": f"act_{i}"})
        for i in range(10)
    ]
    log_path.write_text("\n".join(events), encoding="utf-8")

    # Bust the mtime-based in-process cache.
    from backend.orchestration import plan_vision_check

    plan_vision_check._log_cache.clear()

    score = compute_familiarity_score(
        page_url="http://router/webui/#/dhcp",
        intent=_DHCP_INTENT,
        plan=[{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}],
        settings=settings,
    )

    # success_signal = 0 / (0 + 10 + 1) ≈ 0.0 → score dominated by zero signals.
    assert score < 0.05


# ---------------------------------------------------------------------------
# Test 4: plan_validation_cache succeed_count ≥ 2 → plan_validation_signal = 1.0
# ---------------------------------------------------------------------------


def test_familiarity_score_promotes_after_two_successes(tmp_path: Path) -> None:
    """plan_validation_cache with succeed_count=2 → plan_validation_signal=1.0 → +0.15."""
    settings = _make_settings(tmp_path)
    page_url = "http://router/webui/#/dhcp"
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]

    from backend.webui_agent.vision_fallback import _hash_page_url

    page_k = _hash_page_url(page_url)
    intent_k = _intent_key(_DHCP_INTENT)
    plan_h = _plan_sha1(plan)
    composite_key = f"{page_k}|{intent_k}|{plan_h}"

    cache = {composite_key: {"succeed_count": 2, "last_seen": "2026-05-23T10:00:00Z"}}
    save_plan_validation_cache(settings.plan_validation_cache_path, cache)

    score = compute_familiarity_score(
        page_url=page_url,
        intent=_DHCP_INTENT,
        plan=plan,
        settings=settings,
    )

    # 0.15 * 1.0 = 0.15 contribution from plan_validation_signal alone.
    assert score >= 0.15


# ---------------------------------------------------------------------------
# Test 5: tier 0 skips vision
# ---------------------------------------------------------------------------


def test_tier_0_skips_vision_entirely(tmp_path: Path) -> None:
    """Familiarity ≥ 0.85 → Tier 0 → no Anthropic call, returns PROCEED."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    mock_cls = _make_mock_anthropic("{}")

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.90
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "PROCEED"
    assert result["reason"] == "high_familiarity_skip"
    assert result["tier"] == 0
    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: tier 1 → 1 API call → PROCEED
# ---------------------------------------------------------------------------


def test_tier_1_plan_level_proceed(tmp_path: Path) -> None:
    """Familiarity in [0.55, 0.85) → Tier 1 → 1 Anthropic call → PROCEED."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    response_json = json.dumps({"verdict": "PROCEED", "reason": "fields match", "confidence": 0.9})
    mock_cls = _make_mock_anthropic(response_json)

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.65
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "PROCEED"
    assert result["tier"] == 1
    mock_cls.return_value.messages.create.assert_called_once()
    assert ev.plan_vision_count == 1


# ---------------------------------------------------------------------------
# Test 7: tier 2 → step-level REJECT on field mismatch
# ---------------------------------------------------------------------------


def test_tier_2_step_level_reject_on_field_mismatch(tmp_path: Path) -> None:
    """Familiarity in [0.25, 0.55) → Tier 2 → step-level analysis → REJECT."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    plan = [
        {
            "intent": {"role": "textbox", "name": "Starting IP Address"},
            "action": "fill",
            "value": "255.255.255.0",
        }
    ]

    response_json = json.dumps(
        {
            "verdict": "REJECT",
            "reason": "Starting IP field has subnet mask value",
            "confidence": 0.92,
            "suggested_plan": None,
            "issues": [
                {"step_index": 0, "expected_field": "Starting IP Address", "found_field": "none"}
            ],
        }
    )
    mock_cls = _make_mock_anthropic(response_json)

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.35
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view={"fields": ["Network", "Starting IP Address"]},
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "REJECT"
    assert result["tier"] == 2
    assert result["confidence"] >= 0.7


# ---------------------------------------------------------------------------
# Test 8: DHCP smoke regression — Tier 3 REJECT on known-bad plan
# ---------------------------------------------------------------------------


def test_tier_3_adversarial_rejects_dhcp_bad_plan(tmp_path: Path) -> None:
    """DHCP smoke regression. Known-bad plan (255.255.255.0 in Starting IP, no Network step).

    Mock Haiku returns Tier-3 REJECT. Assert wrapper returns REJECT with the reason.
    """
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    bad_plan = _make_dhcp_bad_plan()

    response_json = json.dumps(
        {
            "verdict": "REJECT",
            "reason": "Starting IP Address field contains a subnet mask (255.255.255.0) instead of an IP; Network field step is missing entirely",
            "confidence": 0.95,
            "risks": [
                "255.255.255.0 is a subnet mask, not a valid starting IP",
                "Network field is required for DHCP pool configuration",
            ],
            "suggested_plan": None,
        }
    )
    mock_cls = _make_mock_anthropic(response_json)

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.10
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=bad_plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "REJECT"
    assert result["tier"] == 3
    assert "255.255.255.0" in result["reason"] or "subnet mask" in result["reason"]
    assert len(result.get("risks", [])) > 0


# ---------------------------------------------------------------------------
# Test 9: REVISE returns suggested_plan
# ---------------------------------------------------------------------------


def test_revise_replaces_plan_and_returns_revise(tmp_path: Path) -> None:
    """Haiku returns REVISE with a corrected suggested_plan → wrapper returns REVISE."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    bad_plan = _make_dhcp_bad_plan()
    good_plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "20.20.20.0"},
        {
            "intent": {"role": "textbox", "name": "Starting IP Address"},
            "action": "fill",
            "value": "20.20.20.1",
        },
    ]

    response_json = json.dumps(
        {
            "verdict": "REVISE",
            "reason": "Network field missing; subnet mask was in wrong field",
            "confidence": 0.88,
            "suggested_plan": good_plan,
            "issues": [],
        }
    )
    mock_cls = _make_mock_anthropic(response_json)

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.20
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=bad_plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "REVISE"
    assert result["suggested_plan"] == good_plan


# ---------------------------------------------------------------------------
# Test 10: Anthropic overload → PROCEED + no raise
# ---------------------------------------------------------------------------


def test_anthropic_overload_returns_proceed(tmp_path: Path) -> None:
    """API exception (simulating 529 overload) → PROCEED with reason=api_error."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("overloaded")
    mock_cls = MagicMock(return_value=mock_client)

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.30
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "PROCEED"
    assert result["reason"] == "api_error"
    # Counter must NOT increment on API failure.
    assert ev.plan_vision_count == 0


# ---------------------------------------------------------------------------
# Test 11: malformed JSON → PROCEED
# ---------------------------------------------------------------------------


def test_malformed_json_returns_proceed(tmp_path: Path) -> None:
    """Non-JSON response from Haiku → PROCEED with reason=malformed_response."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    # _call_haiku_plan_vision calls json.loads() which will raise JSONDecodeError
    # causing the outer except to catch and return api_error (since the exception
    # originates inside the function). For the malformed_response path we need
    # the function to return a string from Anthropic, which isn't the case here
    # since json.loads raises. The malformed_response path is reachable when
    # the top-level result IS a string (raw text passed to check_plan_via_vision
    # directly). We simulate by patching _call_haiku_plan_vision to return a string.
    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.30
        ),
        patch(
            "backend.orchestration.plan_vision_check._call_haiku_plan_vision",
            return_value="Sorry, I cannot validate this plan.",
        ),
    ):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "PROCEED"
    assert result["reason"] == "malformed_response"


# ---------------------------------------------------------------------------
# Test 12: REJECT with confidence < 0.7 → PROCEED
# ---------------------------------------------------------------------------


def test_low_confidence_reject_returns_proceed(tmp_path: Path) -> None:
    """REJECT verdict with confidence 0.5 → downgraded to PROCEED + warn."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    response_json = json.dumps(
        {
            "verdict": "REJECT",
            "reason": "uncertain field mismatch",
            "confidence": 0.5,
            "risks": [],
            "suggested_plan": None,
        }
    )
    mock_cls = _make_mock_anthropic(response_json)

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.20
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "PROCEED"
    assert result["reason"] == "low_confidence_reject"
    assert result["confidence"] == 0.5


# ---------------------------------------------------------------------------
# Test 13: session cap enforcement
# ---------------------------------------------------------------------------


def test_session_cap_enforced(tmp_path: Path) -> None:
    """ev.plan_vision_count = 5 → PROCEED without API call."""
    from backend.orchestration.plan_vision_check import _MAX_PLAN_VISION_CALLS_PER_SESSION

    settings = _make_settings(tmp_path)
    ev = _make_ev()
    ev.plan_vision_count = _MAX_PLAN_VISION_CALLS_PER_SESSION  # already at cap
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    mock_cls = _make_mock_anthropic("{}")

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.20
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "PROCEED"
    assert result["reason"] == "session_cap_reached"
    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Test 14: record_plan_success increments succeed_count
# ---------------------------------------------------------------------------


def test_record_plan_success_increments_succeed_count(tmp_path: Path) -> None:
    """Calling record_plan_success twice → cache shows succeed_count: 2."""
    settings = _make_settings(tmp_path)
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]
    page_url = "http://router/webui/#/dhcp"

    record_plan_success(page_url=page_url, intent=_DHCP_INTENT, plan=plan, settings=settings)
    record_plan_success(page_url=page_url, intent=_DHCP_INTENT, plan=plan, settings=settings)

    cache = load_plan_validation_cache(settings.plan_validation_cache_path)
    assert len(cache) == 1
    entry = next(iter(cache.values()))
    assert entry["succeed_count"] == 2


# ---------------------------------------------------------------------------
# Test 15: cache atomic write (.tmp → rename)
# ---------------------------------------------------------------------------


def test_cache_atomic_write(tmp_path: Path) -> None:
    """save_plan_validation_cache creates .tmp then replaces; no .tmp left after."""
    path = tmp_path / "artifacts" / "plan_validation_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    cache = {"some|key|here": {"succeed_count": 1, "last_seen": "2026-05-23T00:00:00Z"}}
    save_plan_validation_cache(path, cache)

    assert path.exists()
    # The .tmp file should be gone after atomic rename.
    assert not path.with_suffix(".json.tmp").exists()
    # Content is valid JSON.
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == cache


# ---------------------------------------------------------------------------
# Test 16: kill switch disables check
# ---------------------------------------------------------------------------


def test_kill_switch_disables_check(tmp_path: Path) -> None:
    """settings.plan_vision_enabled = False → PROCEED with reason=kill_switch, no API call."""
    settings = _make_settings(tmp_path)
    settings.plan_vision_enabled = False
    ev = _make_ev()
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    mock_cls = _make_mock_anthropic("{}")

    with patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls):
        result = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    assert result["verdict"] == "PROCEED"
    assert result["reason"] == "kill_switch"
    assert result["confidence"] == 1.0
    assert result["tier"] == 0
    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Test 17: intent canonicalization is stable across synonyms
# ---------------------------------------------------------------------------


def test_intent_canonicalization_stable_across_synonyms() -> None:
    """'Configure DHCP' and 'configure dhcp  ' (whitespace, case) produce same intent_key."""
    key1 = _intent_key("Configure DHCP")
    key2 = _intent_key("configure dhcp  ")  # trailing whitespace + different case

    assert key1 == key2


# ---------------------------------------------------------------------------
# Test 18: per-iter check fires on re-draft (low familiarity)
# ---------------------------------------------------------------------------


def test_per_iter_check_runs_on_redraft(tmp_path: Path) -> None:
    """Two calls with different plans and low familiarity both fire vision (if budget allows)."""
    settings = _make_settings(tmp_path)
    ev = _make_ev()
    plan_a = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]
    plan_b = [
        {
            "intent": {"role": "textbox", "name": "Starting IP Address"},
            "action": "fill",
            "value": "10.0.0.1",
        }
    ]

    response_json = json.dumps({"verdict": "PROCEED", "reason": "ok", "confidence": 0.85})
    mock_cls = _make_mock_anthropic(response_json)

    with (
        patch(
            "backend.orchestration.plan_vision_check.compute_familiarity_score", return_value=0.15
        ),
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
    ):
        check_plan_via_vision(
            plan=plan_a,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )
        check_plan_via_vision(
            plan=plan_b,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url="http://router/webui/#/dhcp",
            ev=ev,
            settings=settings,
        )

    # Both calls should have fired API calls (budget = 5, we used 2).
    assert mock_cls.return_value.messages.create.call_count == 2
    assert ev.plan_vision_count == 2


# ---------------------------------------------------------------------------
# _MODEL constant guard
# ---------------------------------------------------------------------------


def test_model_constant_is_haiku_45() -> None:
    assert _MODEL == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Regression: Anthropic client must receive api_key kwarg
# ---------------------------------------------------------------------------
# Live smoke act_20260523_718d70 fired plan_vision_check_api_error 4x with
# "Could not resolve authentication method" because the client was constructed
# as Anthropic(max_retries=N) without api_key=. Every other call site in the
# codebase (planner.py, configure_planner.py, debug_planner.py, routes_suggestions.py)
# passes api_key=get_settings().anthropic_api_key explicitly. Lock that here.


def test_anthropic_client_receives_api_key_kwarg(tmp_path: Path) -> None:
    """_call_haiku_plan_vision MUST pass api_key= to Anthropic(), not rely on env."""
    settings = _make_settings(tmp_path)
    settings.anthropic_api_key = "sk-ant-test-fixture"
    page_url = "http://router/webui/#/dhcp"
    ev = _make_ev()
    intent = "Configure something"
    plan = [{"intent": {"role": "textbox", "name": "Field"}, "action": "fill", "value": "x"}]

    mock_cls = _make_mock_anthropic(
        json.dumps(
            {
                "verdict": "PROCEED",
                "reason": "ok",
                "confidence": 0.9,
            }
        )
    )

    # get_settings is lazy-imported inside _call_haiku_plan_vision; patch at source.
    with (
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        check_plan_via_vision(
            plan=plan,
            intent=intent,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url=page_url,
            ev=ev,
            settings=settings,
        )

    # The Anthropic class was instantiated at least once
    assert mock_cls.called, "Anthropic() was never called"
    # Every construction must include api_key keyword
    for call in mock_cls.call_args_list:
        assert "api_key" in call.kwargs, (
            f"Anthropic constructed without api_key kwarg: {call}. "
            f"Without api_key, the SDK fails with TypeError 'Could not resolve "
            f"authentication method' in some environments. See live smoke "
            f"act_20260523_718d70 for the regression."
        )
        assert call.kwargs["api_key"] == "sk-ant-test-fixture"


# ---------------------------------------------------------------------------
# Regression: snapshot_signal must filter by successful action_ids
# ---------------------------------------------------------------------------
# Audit finding #3 + live smoke act_20260523_41bfa6 evidence: snapshot_signal
# was counting ALL device-snapshots/<id>/post/ dirs, including forensic
# snapshots taken on WriteRejectedError. 5 failed DHCP attempts pushed
# familiarity to 0.427 (Tier 2) for an intent that had never succeeded.
# Tier 3 (adversarial) was the correct tier.


def test_snapshot_signal_filters_by_succeeded_action_ids(tmp_path: Path) -> None:
    """5 post-snapshot dirs with ZERO matching success events → signal == 0.0."""
    settings = _make_settings(tmp_path)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = settings.artifacts_dir / "device-snapshots"

    # Create 5 post-snapshot dirs (simulating WriteRejectedError forensics).
    for i in range(5):
        post_dir = snapshots_dir / f"act_failed_{i}" / "post"
        post_dir.mkdir(parents=True)
        (post_dir / "show_running-config.txt").write_text("blah", encoding="utf-8")

    # Log has only failures — no webui_configure_iteration_complete events.
    log_path = settings.logs_dir / "actions.log"
    events = [
        json.dumps({"event": "webui_act_by_intent_soft_failure", "action_id": f"act_failed_{i}"})
        for i in range(5)
    ]
    log_path.write_text("\n".join(events), encoding="utf-8")

    from backend.orchestration import plan_vision_check

    plan_vision_check._log_cache.clear()

    score = compute_familiarity_score(
        page_url="http://router/webui/#/dhcp",
        intent=_DHCP_INTENT,
        plan=[{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}],
        settings=settings,
    )

    # 5 forensic snapshot dirs but ZERO succeeded action_ids → snapshot_signal = 0.
    # Other signals are also 0 → score < 0.05.
    assert score < 0.05, f"Forensic snapshots inflated familiarity to {score}"


def test_snapshot_signal_counts_only_succeeded(tmp_path: Path) -> None:
    """3 successful action_ids in log + 5 dirs → signal = 3/5 = 0.6 contribution."""
    settings = _make_settings(tmp_path)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = settings.artifacts_dir / "device-snapshots"

    # 5 post-snapshot dirs.
    for i in range(5):
        post_dir = snapshots_dir / f"act_x_{i}" / "post"
        post_dir.mkdir(parents=True)

    # Log marks 3 of them as succeeded.
    log_path = settings.logs_dir / "actions.log"
    events = [
        json.dumps(
            {
                "event": "webui_configure_iteration_complete",
                "action_id": f"act_x_{i}",
                "verify_present": True,
                "batch_clean": True,
            }
        )
        for i in range(3)
    ]
    log_path.write_text("\n".join(events), encoding="utf-8")

    from backend.orchestration import plan_vision_check

    plan_vision_check._log_cache.clear()

    # Direct signal probe (compute_familiarity uses other signals too).
    snap = plan_vision_check._snapshot_signal(settings)
    assert snap == pytest.approx(3 / 5), f"Expected 0.6, got {snap}"


# ---------------------------------------------------------------------------
# Regression: prose-around-JSON vision responses must be recovered
# ---------------------------------------------------------------------------
# Live smoke act_20260523_41bfa6: Haiku returned an empty/prose response,
# json.loads raised inside _call_haiku_plan_vision, outer except caught it
# as api_error → default-PROCEED. We never reached _parse_vision_response.
# Fix: _call_haiku_plan_vision returns raw text; check_plan_via_vision
# routes through brace-extraction recovery.


def test_prose_around_json_response_is_recovered(tmp_path: Path) -> None:
    """Haiku returns prose+JSON like 'I think... {valid JSON}' → recovered + parsed."""
    settings = _make_settings(tmp_path)
    page_url = "http://router/webui/#/dhcp"
    ev = _make_ev()
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]

    # Mock response: prose preamble + valid JSON object (the audit-recommended
    # fallback path).
    prose_response = (
        "Looking at the screenshot, I see a DHCP form. The plan appears correct. "
        '{"verdict": "PROCEED", "reason": "all fields present", "confidence": 0.92}'
    )
    mock_cls = _make_mock_anthropic(prose_response)

    with (
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        verdict = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url=page_url,
            ev=ev,
            settings=settings,
        )

    assert verdict["verdict"] == "PROCEED"
    assert verdict["confidence"] == pytest.approx(0.92)
    assert verdict["reason"] == "all fields present"


def test_empty_haiku_response_falls_through_to_proceed(tmp_path: Path) -> None:
    """Empty content[0].text → malformed_response → PROCEED (not api_error)."""
    settings = _make_settings(tmp_path)
    page_url = "http://router/webui/#/dhcp"
    ev = _make_ev()
    plan = [{"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "x"}]

    # Mock empty response (the live-smoke act_20260523_41bfa6 shape).
    mock_cls = _make_mock_anthropic("")

    with (
        patch("backend.orchestration.plan_vision_check.Anthropic", mock_cls),
        patch("backend.core.settings.get_settings", return_value=settings),
    ):
        verdict = check_plan_via_vision(
            plan=plan,
            intent=_DHCP_INTENT,
            page_screenshot_b64=_MINIMAL_B64,
            view=None,
            rag_chunks=None,
            running_config="",
            page_url=page_url,
            ev=ev,
            settings=settings,
        )

    assert verdict["verdict"] == "PROCEED"
    assert verdict["reason"] == "malformed_response"


# ---------------------------------------------------------------------------
# Option H: filter_executable_steps — drop non-executable actions from
# vision's suggested_plan before tool_registry uses it.
# Live smoke act_20260523_5aa2cf showed vision's suggested_plan includes
# "note" steps with string-typed intent (commentary, not actions). The
# executor would reject those, so we filter before use.
# ---------------------------------------------------------------------------


def test_filter_executable_steps_drops_note_action() -> None:
    from backend.orchestration.plan_vision_check import filter_executable_steps

    plan = [
        {"action": "fill", "intent": {"role": "textbox", "name": "Pool"}, "value": "X"},
        {"action": "note", "intent": "default_gateway", "value": "configure separately"},
        {"action": "click", "intent": {"role": "button", "name": "Apply"}, "value": None},
    ]
    out = filter_executable_steps(plan)
    assert len(out) == 2
    assert out[0]["intent"]["name"] == "Pool"
    assert out[1]["intent"]["name"] == "Apply"


def test_filter_executable_steps_drops_string_intent() -> None:
    """Even if action is in the allowlist, intent must be a dict."""
    from backend.orchestration.plan_vision_check import filter_executable_steps

    plan = [
        {"action": "fill", "intent": "a_string_not_a_dict", "value": "X"},
        {"action": "fill", "intent": {"role": "textbox", "name": "Y"}, "value": "Y"},
    ]
    out = filter_executable_steps(plan)
    assert len(out) == 1
    assert out[0]["intent"]["name"] == "Y"


def test_filter_executable_steps_keeps_all_when_all_valid() -> None:
    from backend.orchestration.plan_vision_check import filter_executable_steps

    plan = [
        {"action": "click", "intent": {"role": "button", "name": "A"}, "value": None},
        {"action": "fill", "intent": {"role": "textbox", "name": "B"}, "value": "x"},
        {"action": "select", "intent": {"role": "combobox", "name": "C"}, "value": "y"},
        {"action": "check", "intent": {"role": "checkbox", "name": "D"}, "value": None},
        {"action": "hover", "intent": {"role": "link", "name": "E"}, "value": None},
    ]
    out = filter_executable_steps(plan)
    assert len(out) == 5


def test_filter_executable_steps_empty_on_all_invalid() -> None:
    from backend.orchestration.plan_vision_check import filter_executable_steps

    plan = [
        {"action": "note", "intent": "x", "value": "y"},
        {"action": "verify", "intent": {"role": "textbox", "name": "Z"}, "value": "..."},
    ]
    out = filter_executable_steps(plan)
    assert out == []


# ---------------------------------------------------------------------------
# Chunk 2 — 3.1: Tier-0 at one success
# ---------------------------------------------------------------------------


def test_plan_validation_signal_one_success_is_tier0(tmp_path: Path) -> None:
    """succeed_count=1 → _plan_validation_signal returns 1.0 (Tier-0 eligible).

    Pre-chunk-2 behaviour was 0.5; one green run is now sufficient to
    earn Tier-0 skip on the next proactive vision check.
    """
    settings = _make_settings(tmp_path)
    page_url = "http://router/webui/#/dhcp"
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]

    from backend.webui_agent.vision_fallback import _hash_page_url

    page_k = _hash_page_url(page_url)
    intent_k = _intent_key(_DHCP_INTENT)
    plan_h = _plan_sha1(plan)
    composite_key = f"{page_k}|{intent_k}|{plan_h}"

    cache = {composite_key: {"succeed_count": 1, "last_seen": "2026-05-30T10:00:00Z"}}
    save_plan_validation_cache(settings.plan_validation_cache_path, cache)

    signal = _plan_validation_signal(page_url, _DHCP_INTENT, plan, settings)

    assert signal == 1.0, (
        f"Expected 1.0 (one success → Tier-0 eligible), got {signal}. "
        "Pre-chunk-2 bug: sc==1 returned 0.5."
    )


def test_plan_validation_signal_zero_successes_is_zero(tmp_path: Path) -> None:
    """succeed_count=0 → signal=0.0 (no Tier-0 skip)."""
    settings = _make_settings(tmp_path)
    page_url = "http://router/webui/#/dhcp"
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]

    from backend.webui_agent.vision_fallback import _hash_page_url

    page_k = _hash_page_url(page_url)
    intent_k = _intent_key(_DHCP_INTENT)
    plan_h = _plan_sha1(plan)
    composite_key = f"{page_k}|{intent_k}|{plan_h}"

    cache = {composite_key: {"succeed_count": 0, "last_seen": "2026-05-30T10:00:00Z"}}
    save_plan_validation_cache(settings.plan_validation_cache_path, cache)

    signal = _plan_validation_signal(page_url, _DHCP_INTENT, plan, settings)

    assert signal == 0.0


def test_familiarity_promotes_to_tier0_after_one_success(tmp_path: Path) -> None:
    """Record one success → plan_validation_signal=1.0 → familiarity ≥ 0.15."""
    settings = _make_settings(tmp_path)
    page_url = "http://router/webui/#/dhcp"
    plan = [
        {"intent": {"role": "textbox", "name": "Network"}, "action": "fill", "value": "10.0.0.0"}
    ]

    record_plan_success(page_url=page_url, intent=_DHCP_INTENT, plan=plan, settings=settings)

    # plan_validation_signal alone contributes 0.15; total score ≥ 0.15.
    score = compute_familiarity_score(page_url, _DHCP_INTENT, plan, settings)
    assert score >= 0.15, f"Expected ≥ 0.15 after one success, got {score}"

    # The signal in isolation must be 1.0.
    signal = _plan_validation_signal(page_url, _DHCP_INTENT, plan, settings)
    assert signal == 1.0
