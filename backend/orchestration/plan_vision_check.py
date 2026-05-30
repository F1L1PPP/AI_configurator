"""Vision pre-check on configure_planner output (chunk 14f-adaptive).

Architecture: PROACTIVE. Invoked BEFORE any step dispatches, both at
proposal time and on each re-draft iteration. Intensity is scaled by
familiarity: well-trodden intent+page combos skip the Anthropic call
entirely; first-time tasks use an adversarial Tier-3 prompt.

Director's thesis: vision cost (~$0.015) is dwarfed by the blast radius
of a router mis-config. Default to MORE vision on first-time tasks; LESS
as the system accumulates evidence the task is well-trodden.

Familiarity formula (weights are intentionally coarse; per-intent
refinement is a follow-up chunk):

    familiarity(page_key, intent_key) =
        0.40 * cache_hit_signal       # selector_cache coverage
      + 0.25 * success_signal         # system-wide EXECUTED ratio
      + 0.20 * snapshot_signal        # post-snapshots in artifacts
      + 0.15 * plan_validation_signal # this exact plan verified ≥ 2x

Tiers:
    0 (≥ 0.85)  — skip entirely, no API call
    1 (0.55–0.85) — single API call, plan-level verdict
    2 (0.25–0.55) — single API call, per-step validation
    3 (< 0.25)  — single API call, adversarial "find what could go wrong"

Default-PROCEED on every failure path to remain non-blocking.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, TypedDict

from anthropic import Anthropic

from backend.core.logging import get_logger
from backend.webui_agent.vision_fallback import _hash_page_url  # reuse URL normalizer

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (locked — do not change without approval).
# ---------------------------------------------------------------------------

_MODEL = "claude-haiku-4-5-20251001"
_MAX_RETRIES = 2
_VISION_TIMEOUT_S = 20
_CONFIDENCE_THRESHOLD = 0.7
_MAX_PLAN_VISION_CALLS_PER_SESSION = 5
# below 0.25 → Tier 3; 0.25–0.55 → Tier 2; 0.55–0.85 → Tier 1; ≥ 0.85 → Tier 0
_TIER_THRESHOLDS = (0.25, 0.55, 0.85)
_SUCCESS_LOG_CACHE_TTL_SECS = 300  # 5-min in-process cache for actions.log reads

# In-process log-read cache: keyed by (path, mtime), value is
# (successes, failures, succeeded_action_ids). The third element is the
# set of action_ids that completed end-to-end (verify_present=true), used
# by _snapshot_signal to filter out forensic snapshots of failed actions.
_log_cache: dict[tuple[str, float], tuple[int, int, frozenset[str]]] = {}


# ---------------------------------------------------------------------------
# Public TypedDict
# ---------------------------------------------------------------------------


class VisionVerdict(TypedDict):
    verdict: Literal["PROCEED", "REVISE", "REJECT"]
    reason: str
    suggested_plan: list[dict] | None
    risks: list[str]
    confidence: float
    tier: int
    familiarity_score: float


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _intent_key(intent: str) -> str:
    """Canonical sha1[:12] for an intent string. Lowercased + stripped."""
    canonical = intent.lower().strip()
    return hashlib.sha1(canonical.encode()).hexdigest()[:12]


def _plan_sha1(plan: list[dict]) -> str:
    """Stable hash of a plan (sort_keys for dict stability)."""
    return hashlib.sha1(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:12]


def load_plan_validation_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Read plan_validation_cache.json; return {} on missing/malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_plan_validation_cache(path: Path, cache: dict) -> None:
    """Atomic write: .tmp sibling → rename. Mirrors selector_cache pattern."""
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Familiarity sub-signals
# ---------------------------------------------------------------------------


def _cache_hit_signal(plan: list[dict], page_url: str, settings: Any) -> float:
    """Ratio of plan steps whose selector is already in selector_cache.

    For each step, compute the canonical (role, name, page_url) cache key
    from vision_fallback and check membership. Returns 0.0 if plan is empty.
    """
    if not plan:
        return 0.0

    from backend.webui_agent.vision_fallback import (  # noqa: PLC0415
        _cache_key,
        load_selector_cache,
    )

    cache = load_selector_cache(settings.selector_cache_path)
    hits = 0
    for step in plan:
        intent = step.get("intent", {})
        role = intent.get("role", "")
        name = intent.get("name", "")
        key = _cache_key(role, name, page_url)
        if key in cache:
            hits += 1

    return hits / len(plan)


def _parse_actions_log(settings: Any) -> tuple[int, int, frozenset[str]]:
    """Parse actions.log once and return (successes, failures, succeeded_action_ids).

    5-minute in-process cache keyed by (path, mtime). Cleared when the file
    changes. Used by _success_signal AND _snapshot_signal — single pass over
    the log avoids duplicated parsing.

    Events:
        webui_act_by_intent_complete     → step-level success counter
        webui_act_by_intent_soft_failure → step-level failure counter
        webui_configure_iteration_complete (verify_present=true, batch_clean=true)
            → action_id added to succeeded set (end-to-end success marker)
    """
    global _log_cache  # noqa: PLW0603

    logs_dir: Path = settings.logs_dir
    log_path = logs_dir / "actions.log"

    try:
        mtime = log_path.stat().st_mtime
    except OSError:
        return (0, 0, frozenset())

    cache_key = (str(log_path), mtime)

    # Purge stale entries for the same path (avoids unbounded growth).
    stale = [k for k in _log_cache if k[0] == str(log_path) and k != cache_key]
    for k in stale:
        del _log_cache[k]

    if cache_key in _log_cache:
        return _log_cache[cache_key]

    successes = 0
    failures = 0
    succeeded_action_ids: set[str] = set()
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            with suppress(json.JSONDecodeError, KeyError):
                event = json.loads(line)
                ev_type = event.get("event", "")
                if ev_type == "webui_act_by_intent_complete":
                    successes += 1
                elif ev_type == "webui_act_by_intent_soft_failure":
                    failures += 1
                elif (
                    ev_type == "webui_configure_iteration_complete"
                    and event.get("verify_present") is True
                    and event.get("batch_clean") is True
                ):
                    aid = event.get("action_id")
                    if isinstance(aid, str):
                        succeeded_action_ids.add(aid)
    except OSError:
        pass

    result = (successes, failures, frozenset(succeeded_action_ids))
    _log_cache[cache_key] = result
    return result


def _success_signal(settings: Any) -> float:
    """System-wide success ratio from actions.log.

    +1 denominator: avoids division by zero and slight pessimism on tiny samples.
    """
    successes, failures, _ = _parse_actions_log(settings)
    return successes / (successes + failures + 1)


def _snapshot_signal(settings: Any) -> float:
    """Count of post-snapshot dirs FROM SUCCESSFUL ACTIONS only (clip at 5, /5).

    Gaming defense (audit finding #3): snapshots are also taken on
    WriteRejectedError + verify-failure for forensic preservation. Without
    this filter, 5 failed actions would push familiarity by +0.20 and demote
    Tier 3 (adversarial) to Tier 2 (step-level) for tasks never actually
    successfully performed. We cross-reference against the log's set of
    action_ids that completed end-to-end (webui_configure_iteration_complete
    with verify_present=true).
    """
    snapshots_dir: Path = settings.artifacts_dir / "device-snapshots"
    if not snapshots_dir.exists():
        return 0.0

    _, _, succeeded_action_ids = _parse_actions_log(settings)

    count = 0
    for child in snapshots_dir.iterdir():
        if not (child.is_dir() and (child / "post").is_dir()):
            continue
        if child.name in succeeded_action_ids:
            count += 1
            if count >= 5:
                break

    return count / 5


def _plan_validation_signal(
    page_url: str,
    intent: str,
    plan: list[dict],
    settings: Any,
) -> float:
    """Check if this exact (page, intent, plan_hash) trio has been verified before.

    Returns 1.0 if succeed_count ≥ 1, 0.0 otherwise.
    One green run is enough to earn Tier-0 skip on the next run — the
    blast-radius cost of a mis-config dwarfs the cost of an extra vision call
    on first encounter; there is no middle tier (0.5) once a plan has
    succeeded at least once.
    """
    cache = load_plan_validation_cache(settings.plan_validation_cache_path)
    page_k = _hash_page_url(page_url)
    intent_k = _intent_key(intent)
    plan_h = _plan_sha1(plan)
    composite_key = f"{page_k}|{intent_k}|{plan_h}"
    entry = cache.get(composite_key)
    if entry is None:
        return 0.0
    sc = entry.get("succeed_count", 0)
    return 1.0 if sc >= 1 else 0.0


# ---------------------------------------------------------------------------
# Public familiarity scorer
# ---------------------------------------------------------------------------


def compute_familiarity_score(
    page_url: str,
    intent: str,
    plan: list[dict[str, Any]],
    settings: Any,
) -> float:
    """Score in [0, 1]. Higher = more familiar = less vision needed.

    Weighted formula:
        0.40 * cache_hit_signal
        0.25 * success_signal
        0.20 * snapshot_signal
        0.15 * plan_validation_signal
    """
    ch = _cache_hit_signal(plan, page_url, settings)
    ss = _success_signal(settings)
    snap = _snapshot_signal(settings)
    pv = _plan_validation_signal(page_url, intent, plan, settings)

    raw = 0.40 * ch + 0.25 * ss + 0.20 * snap + 0.15 * pv
    return max(0.0, min(1.0, raw))


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------


def _assign_tier(familiarity: float) -> int:
    lo, mid, hi = _TIER_THRESHOLDS
    if familiarity >= hi:
        return 0
    if familiarity >= mid:
        return 1
    if familiarity >= lo:
        return 2
    return 3


# ---------------------------------------------------------------------------
# Anthropic vision calls per tier
# ---------------------------------------------------------------------------


def _build_image_block(b64: str) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def _build_text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _call_haiku_plan_vision(
    tier: int,
    plan: list[dict],
    intent: str,
    screenshot_b64: str,
    view: dict[str, Any] | None,
    rag_chunks: list[dict[str, Any]] | None,
    running_config: str,
) -> str:
    """Make the Anthropic vision call for a given tier. Returns RAW response text.

    Returns the raw text — caller MUST route through _parse_vision_response
    so prose-around-JSON responses are recovered via brace-extraction. Returns
    empty string if Haiku produces no text content. Raises on API failure
    (network, 5xx, auth) — those are caught by the outer handler.
    """
    from backend.core.settings import get_settings  # noqa: PLC0415

    client = Anthropic(
        api_key=get_settings().anthropic_api_key,
        max_retries=_MAX_RETRIES,
        timeout=_VISION_TIMEOUT_S,
    )

    content: list[dict[str, Any]] = [_build_image_block(screenshot_b64)]

    plan_json = json.dumps(plan, indent=2)
    base_context = f"Intent: {intent}\n\nProposed plan:\n```json\n{plan_json}\n```\n"

    if tier == 1:
        # Plan-level: simple pass/reject.
        content.append(
            _build_text_block(
                base_context + "Look at the screenshot and the proposed plan. "
                "Does this plan correctly match the visible UI fields for the stated intent? "
                'Return ONLY JSON: {"verdict": "PROCEED" or "REJECT", "reason": "...", '
                '"confidence": 0.0-1.0}'
            )
        )

    elif tier == 2:
        # Step-level: validate each step against visible fields.
        describe_text = ""
        if view:
            describe_text = f"\nPage description:\n{json.dumps(view, indent=2)}\n"
        content.append(
            _build_text_block(
                base_context + describe_text + "Validate each step against the visible UI. "
                "Check that field names in the plan match real field names on the page. "
                'Return ONLY JSON: {"verdict": "PROCEED" or "REVISE" or "REJECT", '
                '"reason": "...", "confidence": 0.0-1.0, '
                '"suggested_plan": <revised plan list or null>, '
                '"issues": [{"step_index": N, "expected_field": "...", "found_field": "..."}]}'
            )
        )

    else:
        # Tier 3 — adversarial: find what could go wrong.
        rag_text = ""
        if rag_chunks:
            chunk_snippets = "\n---\n".join(
                c.get("text", "") for c in rag_chunks[:3] if c.get("text")
            )
            if chunk_snippets:
                rag_text = f"\nRelevant documentation:\n{chunk_snippets}\n"
        running_cfg_text = ""
        if running_config:
            # Clip at 4 KB for this prompt — full config goes to vision_fallback
            clipped = running_config[:4096]
            running_cfg_text = f"\nRouter running-config snippet:\n```\n{clipped}\n```\n"

        content.append(
            _build_text_block(
                base_context
                + rag_text
                + running_cfg_text
                + "This is a first-time or low-familiarity task. "
                "Act as a hostile reviewer. "
                "Find every way this plan could mis-configure the router: wrong field names, "
                "wrong field order, IP addresses placed in wrong inputs, missing required fields, "
                "steps that target non-existent UI elements, etc. "
                'Return ONLY JSON: {"verdict": "PROCEED" or "REVISE" or "REJECT", '
                '"reason": "...", "confidence": 0.0-1.0, '
                '"risks": ["...", ...], '
                '"suggested_plan": <corrected plan list or null>}'
            )
        )

    response = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],  # type: ignore[typeddict-item]
    )
    # Return raw text — caller routes through _parse_vision_response which
    # handles prose-around-JSON via brace extraction. Live smoke
    # act_20260523_41bfa6 hit JSONDecodeError on empty/prose responses;
    # parsing inline here was eating recoverable cases.
    # First content block is always TextBlock in our prompts (no tools).
    first_block = response.content[0]
    return (first_block.text if hasattr(first_block, "text") else "") or ""


def _extract_first_json_object_local(text: str) -> str | None:
    """Brace-balanced JSON extraction from raw text. Mirrors configure_planner."""
    depth = 0
    in_string = False
    escape = False
    start = -1
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : i + 1]
    return None


def _parse_vision_response(raw: str) -> dict[str, Any] | None:
    """Try strict JSON parse; fall back to brace-extraction."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        extracted = _extract_first_json_object_local(raw)
        if extracted:
            with suppress(json.JSONDecodeError):
                return json.loads(extracted)
    return None


# ---------------------------------------------------------------------------
# Vision rejection dump helper
# ---------------------------------------------------------------------------


def _dump_vision_rejection(action_id: str, verdict: VisionVerdict, settings: Any) -> None:
    """Write verdict JSON to artifacts/vision-rejections/<action_id>.json."""
    rejection_dir = settings.artifacts_dir / "vision-rejections"
    rejection_dir.mkdir(parents=True, exist_ok=True)
    out_path = rejection_dir / f"{action_id}.json"
    tmp = out_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(dict(verdict), indent=2), encoding="utf-8")
        tmp.replace(out_path)
        log.info("vision_rejection_dumped", action_id=action_id, path=str(out_path))
    except OSError as exc:
        log.warning(
            "vision_rejection_dump_failed",
            action_id=action_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Suggested-plan filtering (chunk 14h follow-up to 14g)
# ---------------------------------------------------------------------------

# Actions the WebUI executor knows how to dispatch via _do_act. Vision's
# suggested_plan may include narrative or commentary actions like "note" or
# "verify" that the executor would reject — strip them before use.
_EXECUTABLE_ACTIONS = frozenset({"click", "fill", "select", "check", "hover"})


def filter_executable_steps(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop non-executable actions from a vision-suggested plan.

    Vision occasionally adds "note" / "verify" / "configure_separately" steps
    that explain something but aren't actions the executor can dispatch.
    Keeps only steps whose ``action`` is in _EXECUTABLE_ACTIONS AND whose
    ``intent`` is a dict (filters out string-typed intents from notes).
    """
    out: list[dict[str, Any]] = []
    for step in plan:
        if not isinstance(step, dict):
            continue
        if step.get("action") not in _EXECUTABLE_ACTIONS:
            continue
        if not isinstance(step.get("intent"), dict):
            continue
        out.append(step)
    return out


# ---------------------------------------------------------------------------
# Success cache updater
# ---------------------------------------------------------------------------


def record_plan_success(
    page_url: str,
    intent: str,
    plan: list[dict],
    settings: Any,
) -> None:
    """Bump succeed_count for this (page, intent, plan_hash) in the validation cache."""
    cache_path: Path = settings.plan_validation_cache_path
    cache = load_plan_validation_cache(cache_path)

    page_k = _hash_page_url(page_url)
    intent_k = _intent_key(intent)
    plan_h = _plan_sha1(plan)
    composite_key = f"{page_k}|{intent_k}|{plan_h}"

    entry = cache.get(composite_key, {"succeed_count": 0, "last_seen": None})
    entry["succeed_count"] = entry.get("succeed_count", 0) + 1
    entry["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cache[composite_key] = entry

    try:
        save_plan_validation_cache(cache_path, cache)
    except OSError as exc:
        log.warning(
            "plan_validation_cache_save_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def check_plan_via_vision(
    plan: list[dict[str, Any]],
    intent: str,
    page_screenshot_b64: str,
    view: dict[str, Any] | None,
    rag_chunks: list[dict[str, Any]] | None,
    running_config: str,
    page_url: str,
    ev: Any,
    settings: Any,
) -> VisionVerdict:
    """Vision-validate the planner's output. Never raises.

    ``ev`` may be None (proposal-time calls where no EvidenceCollector is
    in scope). When None, the per-session budget counter is not tracked —
    the vision call still fires but the count goes nowhere.

    Returns VisionVerdict with one of: PROCEED, REVISE, REJECT.
    REJECT with confidence < 0.7 is automatically downgraded to PROCEED.
    """
    # Kill switch — operator can disable entirely via env var.
    if not getattr(settings, "plan_vision_enabled", True):
        log.debug("plan_vision_check_disabled_by_kill_switch")
        return VisionVerdict(
            verdict="PROCEED",
            reason="kill_switch",
            suggested_plan=None,
            risks=[],
            confidence=1.0,
            tier=0,
            familiarity_score=1.0,
        )

    # Familiarity score determines tier.
    familiarity = compute_familiarity_score(page_url, intent, plan, settings)
    tier = _assign_tier(familiarity)

    log.info(
        "plan_vision_check_start",
        intent=intent[:80],
        familiarity=round(familiarity, 3),
        tier=tier,
        plan_steps=len(plan),
    )

    # Tier 0 — high familiarity, skip API call entirely.
    if tier == 0:
        log.info("plan_vision_check_tier0_skip", familiarity=round(familiarity, 3))
        return VisionVerdict(
            verdict="PROCEED",
            reason="high_familiarity_skip",
            suggested_plan=None,
            risks=[],
            confidence=1.0,
            tier=0,
            familiarity_score=familiarity,
        )

    # Per-session budget guard (before Anthropic call).
    # ev=None → proposal-time caller without EvidenceCollector; treat as fresh (count=0).
    vision_count = getattr(ev, "plan_vision_count", 0) if ev is not None else 0
    if vision_count >= _MAX_PLAN_VISION_CALLS_PER_SESSION:
        log.warning(
            "plan_vision_check_session_cap_reached",
            count=vision_count,
            cap=_MAX_PLAN_VISION_CALLS_PER_SESSION,
            intent=intent[:80],
        )
        return VisionVerdict(
            verdict="PROCEED",
            reason="session_cap_reached",
            suggested_plan=None,
            risks=[],
            confidence=0.0,
            tier=tier,
            familiarity_score=familiarity,
        )

    # Call Haiku vision.
    try:
        raw_result = _call_haiku_plan_vision(
            tier=tier,
            plan=plan,
            intent=intent,
            screenshot_b64=page_screenshot_b64,
            view=view,
            rag_chunks=rag_chunks,
            running_config=running_config,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "plan_vision_check_api_error",
            intent=intent[:80],
            tier=tier,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return VisionVerdict(
            verdict="PROCEED",
            reason="api_error",
            suggested_plan=None,
            risks=[],
            confidence=0.0,
            tier=tier,
            familiarity_score=familiarity,
        )

    # Increment counter AFTER successful return (exceptions don't count).
    if ev is not None:
        ev.plan_vision_count += 1

    # raw_result is the raw response text from Haiku. Route through
    # _parse_vision_response so prose-around-JSON ("Looking at the screenshot,
    # I think... {...}") is recovered via brace-extraction. Empty/non-JSON
    # responses fall through to default-PROCEED.
    parsed = _parse_vision_response(raw_result) if raw_result else None
    if parsed is None:
        log.warning(
            "plan_vision_check_malformed_response",
            intent=intent[:80],
            raw=raw_result[:200] if raw_result else "",
        )
        return VisionVerdict(
            verdict="PROCEED",
            reason="malformed_response",
            suggested_plan=None,
            risks=[],
            confidence=0.0,
            tier=tier,
            familiarity_score=familiarity,
        )
    result = parsed

    raw_verdict = result.get("verdict", "PROCEED")
    reason = result.get("reason", "")
    confidence = float(result.get("confidence", 0.0))
    suggested_plan = result.get("suggested_plan")
    risks = result.get("risks", [])

    # Normalize verdict to known values.
    if raw_verdict not in ("PROCEED", "REVISE", "REJECT"):
        raw_verdict = "PROCEED"

    # REJECT with low confidence → downgrade to PROCEED (defensive).
    if raw_verdict == "REJECT" and confidence < _CONFIDENCE_THRESHOLD:
        log.warning(
            "plan_vision_check_low_confidence_reject_downgraded",
            intent=intent[:80],
            confidence=confidence,
            original_reason=reason,
        )
        return VisionVerdict(
            verdict="PROCEED",
            reason="low_confidence_reject",
            suggested_plan=None,
            risks=risks,
            confidence=confidence,
            tier=tier,
            familiarity_score=familiarity,
        )

    log.info(
        "plan_vision_check_done",
        intent=intent[:80],
        verdict=raw_verdict,
        confidence=round(confidence, 3),
        tier=tier,
        familiarity=round(familiarity, 3),
        reason=reason[:120] if reason else "",
    )

    return VisionVerdict(
        verdict=raw_verdict,
        reason=reason,
        suggested_plan=suggested_plan if isinstance(suggested_plan, list) else None,
        risks=risks if isinstance(risks, list) else [],
        confidence=confidence,
        tier=tier,
        familiarity_score=familiarity,
    )
