"""Parent-side helper for running a Playwright flow in a child Python process.

Pair to `_playwright_subprocess.py`. The flow modules call
`run_flow_in_subprocess("add_access_vlan", {...})` instead of importing
Playwright directly. The child handles all Playwright work; this helper
parses the JSON result and surfaces failures as exceptions the flow's
existing `except` block can deal with.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from backend.core.logging import get_logger

log = get_logger(__name__)

# Hard ceiling on child runtime. The frontend execute watchdog caps at
# 90s; we go slightly higher so the UI gives up first (and the user
# sees the friendly "Execution timed out" message rather than a more
# generic subprocess error). If a flow legitimately needs longer, bump
# both this and the frontend cap together.
DEFAULT_SUBPROCESS_TIMEOUT_S = 120.0


class SubprocessFlowError(RuntimeError):
    """Raised when the child Python process for a Playwright flow failed.

    `error` and `exc_type` come from the child's JSON output. `stderr`
    is the captured stderr (full traceback if the child set one).
    """

    def __init__(
        self,
        flow: str,
        error: str,
        exc_type: str,
        stderr: str,
    ) -> None:
        super().__init__(f"{flow} subprocess failed: {exc_type}: {error}")
        self.flow = flow
        self.error = error
        self.exc_type = exc_type
        self.stderr = stderr


def run_flow_in_subprocess(
    flow: str,
    args: dict[str, Any],
    *,
    timeout_s: float = DEFAULT_SUBPROCESS_TIMEOUT_S,
) -> dict[str, Any]:
    """Spawn a child Python that runs the Playwright portion of a flow.

    The child reads `{"flow": flow, "args": args}` from stdin, runs the
    Playwright steps, and writes either:
        - `{"ok": true,  "result": {...}}` on success → returned as-is
        - `{"ok": false, "error": str, "exc_type": str}` on failure → raises

    Args:
        flow:       Name registered in
                    `_playwright_subprocess._DISPATCH` — e.g.
                    "add_access_vlan", "change_hostname".
        args:       JSON-serialisable kwargs forwarded to the handler.
        timeout_s:  Hard cap; subprocess is killed on overrun.

    Raises:
        SubprocessFlowError:  child exited non-zero or returned ok=false
        subprocess.TimeoutExpired: child didn't finish within timeout_s
        RuntimeError:         child stdout was unparseable
    """
    payload = json.dumps({"flow": flow, "args": args})
    log.info(
        "playwright_subprocess_start",
        flow=flow,
        timeout_s=timeout_s,
    )
    proc = subprocess.run(
        [sys.executable, "-m", "backend.webui_agent._playwright_subprocess"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )

    if not proc.stdout.strip():
        # No JSON on stdout = catastrophic child failure (import error,
        # segfault, etc.). Stderr usually has the cause.
        raise SubprocessFlowError(
            flow=flow,
            error="no JSON output from subprocess (likely import/startup failure)",
            exc_type="EmptyOutput",
            stderr=proc.stderr,
        )

    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SubprocessFlowError(
            flow=flow,
            error=f"subprocess emitted invalid JSON: {exc!s}",
            exc_type="JSONDecodeError",
            stderr=proc.stderr,
        ) from exc

    if not body.get("ok"):
        # Child reported a clean failure. Re-raise with full context.
        log.error(
            "playwright_subprocess_failed",
            flow=flow,
            exc_type=body.get("exc_type"),
            error=body.get("error"),
            stderr_tail=proc.stderr[-500:] if proc.stderr else "",
        )
        raise SubprocessFlowError(
            flow=flow,
            error=str(body.get("error", "<no error message>")),
            exc_type=str(body.get("exc_type", "Unknown")),
            stderr=proc.stderr,
        )

    log.info(
        "playwright_subprocess_complete",
        flow=flow,
        result_keys=sorted((body.get("result") or {}).keys()),
    )
    return body.get("result") or {}
