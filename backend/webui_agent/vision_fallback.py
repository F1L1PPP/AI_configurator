"""Vision fallback for ``unknown_eid`` failures in the WebUI agent.

When ``_do_act_by_intent`` cannot resolve an intent via semantic-DOM forward
lookup OR ``login.first_match`` (i.e. both paths return None), this module
screenshots the current page, sends it to Claude Haiku 4.5 vision along with
grounding context (prior screenshots from the same page + the latest
router running-config), and asks for a stable Playwright selector.

Architecture: REACTIVE. Invoked only by ``_do_act_by_intent`` on
``unknown_eid``. Not triggered by ``element_missing`` / ``element_hidden``
/ ``element_intercepted``.

Self-training: every successful resolution (confidence >= 0.7) is written to
``artifacts/selector_cache.json``. On subsequent calls with the same
(role, name, page URL), the cache is hit and Anthropic is NOT called.

Security note: screenshots may contain router IP addresses and partial
running-config text. Content is transmitted to Anthropic over HTTPS.
Acceptable for the demo lab; screenshot redaction is a future chunk (14c).
"""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from backend.core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (locked — do not change without approval).
# ---------------------------------------------------------------------------

_MODEL = "claude-haiku-4-5-20251001"
_MAX_RETRIES = 5
_CONFIDENCE_THRESHOLD = 0.7
_MAX_PRIOR_SCREENSHOTS = 2
_MAX_RUNNING_CONFIG_BYTES = 8192
_MAX_VISION_CALLS_PER_SESSION = 15  # bumped from 5 in chunk 14g (vision-first
# inversion fires vision on every action; first-ever DHCP/OSPF/NAT forms have
# 6-10 fields. Cap of 5 would burn during initial page warm-up and trigger
# heuristic fallback for the remaining fields — defeating the whole point of
# vision-first. Cache hits don't increment, so this only bounds first-encounter
# spend at $0.015 × 15 = $0.225 per session.


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _hash_page_url(page_url: str) -> str:
    """Normalize (strip query, strip fragment, lowercase host), sha1[:12]."""
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(page_url)
    # Reconstruct without query or fragment; lowercase scheme+host.
    normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]


def _cache_key(role: str, name: str, page_url: str) -> str:
    """Build a stable lookup key independent of query params and timestamps."""
    return f"{role}|{name}|{_hash_page_url(page_url)}"


def load_selector_cache(path: Path) -> dict[str, str]:
    """Read selector_cache.json; return {} on missing/malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_selector_cache(path: Path, cache: dict[str, str]) -> None:
    """Atomic write: write to a .tmp sibling, then rename over the real file."""
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(path)


def evict_from_selector_cache(path: Path, role: str, name: str, page_url: str) -> bool:
    """Remove (role, name, page_url) from the cache. Returns True if evicted.

    Called when a cached selector's action fails with a staleness signal
    (element_hidden, element_disabled, element_intercepted). The next
    resolve_via_vision call then goes to Anthropic instead of returning
    the stale cached selector.
    """
    cache = load_selector_cache(path)
    key = _cache_key(role, name, page_url)
    if key not in cache:
        return False
    del cache[key]
    try:
        save_selector_cache(path, cache)
    except OSError as exc:
        log.warning(
            "selector_cache_evict_failed",
            key=key,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
    log.info("selector_cache_evicted", key=key)
    return True


# ---------------------------------------------------------------------------
# Screenshot / running-config discovery
# ---------------------------------------------------------------------------


def _find_prior_screenshots(
    screenshots_dir: Path,
    page_url: str,
    max_n: int = _MAX_PRIOR_SCREENSHOTS,
) -> list[Path]:
    """Walk artifacts/screenshots/*/*/NN-label.png for matching page URL.

    Match heuristic: path contains the URL's path tail (e.g. ``ospf`` for
    ``/webui/#/ospf``, ``dhcp`` for ``/webui/#/dhcp``). Most-recent first by
    mtime. Caps at max_n.
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(page_url)
    # For hash-routed SPAs like /webui/#/dhcp, the tail is in the fragment.
    path_tail = (parsed.fragment or parsed.path).strip("/").split("/")[-1].lower()

    if not path_tail or not screenshots_dir.exists():
        return []

    candidates: list[Path] = []
    for png in screenshots_dir.rglob("*.png"):
        # Use the lowercased relative path so we catch subdirectory names too.
        if path_tail in str(png).lower():
            candidates.append(png)

    # Sort by modification time descending (most recent first).
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:max_n]


def _latest_post_running_config(snapshots_dir: Path) -> str | None:
    """Return content of most-recent post/show_running-config.txt.

    Walks ``artifacts/device-snapshots/*/post/show_running-config.txt``,
    picks freshest by mtime, reads with 8 KB cap.  Returns None on no match.
    Truncated content ends with ``\\n# [truncated at 8192 bytes]\\n``.
    """
    if not snapshots_dir.exists():
        return None

    matches = list(snapshots_dir.glob("*/post/show_running-config.txt"))
    if not matches:
        return None

    latest = max(matches, key=lambda p: p.stat().st_mtime)
    try:
        raw = latest.read_bytes()
    except OSError:
        return None

    if len(raw) > _MAX_RUNNING_CONFIG_BYTES:
        text = raw[:_MAX_RUNNING_CONFIG_BYTES].decode("utf-8", errors="replace")
        return text + "\n# [truncated at 8192 bytes]\n"
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# JSON-from-prose recovery (mirrors plan_vision_check._extract_first_json_object_local)
# ---------------------------------------------------------------------------


def _extract_first_json_object(text: str) -> str | None:
    """Brace-balanced JSON extraction from raw text. Returns the substring of
    the first complete top-level {...} object, or None if no balanced object
    is found. Used to recover JSON from Haiku responses that wrap the JSON
    in prose ("Looking at the screenshot, I see... {valid JSON}").
    """
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


# ---------------------------------------------------------------------------
# Anthropic vision call
# ---------------------------------------------------------------------------


def _call_haiku_vision(
    current_b64: str,
    prior_b64s: list[str],
    running_config: str | None,
    role: str,
    name: str,
) -> dict[str, Any]:
    """Make the Anthropic vision call.  Returns parsed JSON dict or raises."""
    from backend.core.settings import get_settings  # noqa: PLC0415

    client = Anthropic(api_key=get_settings().anthropic_api_key, max_retries=_MAX_RETRIES)

    content: list[dict[str, Any]] = []

    # Current page screenshot (always first).
    content.append(
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": current_b64},
        }
    )

    # Prior screenshots (0-2) as additional context.
    for prior_b64 in prior_b64s:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": prior_b64},
            }
        )

    # Running-config text (optional).
    if running_config:
        content.append(
            {
                "type": "text",
                "text": f"Current router running-config:\n```\n{running_config}\n```\n",
            }
        )

    # Intent prompt — always last.
    # Selector-uniqueness clauses added in chunk 14h-D after live smoke
    # act_20260523_90c146 returned `button:has-text('Add')` which matched
    # multiple buttons on the page → click hung → 30s timeout cascade.
    content.append(
        {
            "type": "text",
            "text": (
                f"Find the element with role='{role}' and accessible name '{name}'. "
                "Return ONLY JSON: "
                '{"selector": "...", "confidence": 0.0-1.0, "reasoning": "..."}.\n\n'
                "CRITICAL: the selector MUST match EXACTLY ONE visible element on the "
                "page. Bare role+text selectors like `button:has-text('Add')` are "
                "FORBIDDEN if multiple Add buttons exist (e.g. column headers, table "
                "rows, modal). Prefer in this order:\n"
                "  1. `input[name='attr_name']` or `[id='exact-id']` — HTML attribute "
                "scoped (read the DOM if visible).\n"
                "  2. `[aria-label='Exact Label']` — accessibility-name match.\n"
                "  3. Container-scoped: `.modal button:has-text('Apply')` or "
                "`form[name='dhcp'] input[name='networkIp']` — parent selector + child.\n"
                "  4. Last resort: `:nth-match(:visible, N)` where N targets the "
                "specific occurrence.\n"
                "NEVER: xpath, `:nth-child`, bare text selectors that could collide."
            ),
        }
    )

    response = client.messages.create(
        model=_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": content}],  # type: ignore[typeddict-item]
    )

    # First content block is always a TextBlock in our prompts (no tools).
    first_block = response.content[0]
    raw_text = first_block.text if hasattr(first_block, "text") else ""

    # Live smoke act_20260523_6dc28c showed Haiku returning empty/prose
    # content (JSONDecodeError at column 1) for every selector resolution.
    # Mirror the plan_vision_check fix (commit 27a0421): try strict parse,
    # fall back to brace extraction for prose-around-JSON, raise with a
    # clear message on hard failure so the outer except logs an actionable
    # api_error instead of an opaque JSONDecodeError.
    if not raw_text.strip():
        raise ValueError("Vision response had empty text content")
    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        extracted = _extract_first_json_object(raw_text)
        if extracted is None:
            raise ValueError(
                f"Vision response was prose with no JSON object: {raw_text[:200]}"
            ) from None
        result = json.loads(extracted)

    # Validate required keys are present before returning.
    if "selector" not in result or "confidence" not in result:
        raise ValueError(f"Vision response missing required keys: {raw_text[:200]}")

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def resolve_via_vision(
    page: Any,
    intent: dict[str, Any],
    ev: Any,
    settings: Any,
) -> str | None:
    """Resolve intent -> Playwright selector via Claude vision.

    Returns selector string on success (>= 0.7 confidence) or None on
    any failure (low confidence, malformed response, API error, etc.).
    On success, also writes (cache_key, selector) to the cache.
    Never raises — caller treats None as 'fall through to unknown_eid'.
    """
    role = intent.get("role")
    name = intent.get("name")

    if not isinstance(role, str) or not isinstance(name, str):
        log.debug("vision_fallback_skipped_bad_intent", role=role, name=name)
        return None

    page_url: str = page.url
    key = _cache_key(role, name, page_url)

    # 1. Cache hit — skip Anthropic entirely.
    cache_path: Path = settings.selector_cache_path
    cache = load_selector_cache(cache_path)
    if key in cache:
        log.info("vision_fallback_cache_hit", key=key, selector=cache[key])
        return cache[key]

    # Per-session budget guard — bound spend if Haiku misfires on a tricky page.
    if ev.vision_call_count >= _MAX_VISION_CALLS_PER_SESSION:
        log.warning(
            "vision_fallback_session_cap_reached",
            count=ev.vision_call_count,
            cap=_MAX_VISION_CALLS_PER_SESSION,
            role=role,
            name=name,
        )
        return None

    # 2. Take a screenshot of the current page.
    intent_id = hashlib.sha1(f"{role}|{name}".encode()).hexdigest()[:12]
    try:
        screenshot_path: Path = ev.vision_screenshot(page, intent_id)
        current_b64 = base64.b64encode(screenshot_path.read_bytes()).decode()
    except Exception:  # noqa: BLE001
        log.warning("vision_fallback_screenshot_failed", role=role, name=name)
        return None

    # 3. Find prior screenshots for grounding context.
    screenshots_dir = settings.artifacts_dir / "screenshots"
    prior_paths = _find_prior_screenshots(screenshots_dir, page_url)
    prior_b64s: list[str] = []
    for p in prior_paths:
        with suppress(OSError):
            prior_b64s.append(base64.b64encode(p.read_bytes()).decode())

    # 4. Load running-config for grounding context.
    snapshots_dir = settings.artifacts_dir / "device-snapshots"
    running_config = _latest_post_running_config(snapshots_dir)

    # 5. Call Haiku vision.
    try:
        result = _call_haiku_vision(current_b64, prior_b64s, running_config, role, name)
        ev.vision_call_count += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("vision_fallback_api_error", role=role, name=name, error=str(exc))
        return None

    confidence = result.get("confidence", 0.0)
    selector = result.get("selector")

    if not isinstance(selector, str) or confidence < _CONFIDENCE_THRESHOLD:
        log.info(
            "vision_fallback_low_confidence",
            role=role,
            name=name,
            confidence=confidence,
        )
        return None

    # 6. Cache the successful resolution.
    cache[key] = selector
    try:
        save_selector_cache(cache_path, cache)
    except OSError as exc:
        log.warning("vision_fallback_cache_write_failed", error=str(exc))

    log.info(
        "vision_fallback_resolved",
        role=role,
        name=name,
        selector=selector,
        confidence=confidence,
        reasoning=result.get("reasoning", ""),
    )
    return selector
