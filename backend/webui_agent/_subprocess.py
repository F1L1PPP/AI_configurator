"""Parent-side helper for running a Playwright flow in a child Python process.

Pair to `_playwright_subprocess.py`. Provides two shapes:

1. `run_flow_in_subprocess(flow, args)` — one-shot mode, used by the
   hand-coded fast-path flows (`flows/change_hostname.py`,
   `flows/add_access_vlan.py`). Spawn → run → exit.

2. `WebUISession` class — Phase 4 long-lived session for the
   AI-driven generic driver. Spawn → login once → JSON-line message
   loop → close. Used by `generic_driver.py` to keep the Page +
   locator_map alive across multiple planner-issued ops in a single
   turn.

The child handles all Playwright work; this helper parses JSON results
and surfaces failures as exceptions the caller can deal with.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
from types import TracebackType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

from backend.core.logging import get_logger

log = get_logger(__name__)

# Hard ceiling on child runtime. The frontend execute watchdog caps at
# 90s; we go slightly higher so the UI gives up first (and the user
# sees the friendly "Execution timed out" message rather than a more
# generic subprocess error). If a flow legitimately needs longer, bump
# both this and the frontend cap together.
DEFAULT_SUBPROCESS_TIMEOUT_S = 120.0


# ---------------------------------------------------------------------------
# Subprocess stderr forwarding — NDJSON → parent structlog
# ---------------------------------------------------------------------------


def _forward_subprocess_stderr_lines(raw_lines: Iterable[str]) -> None:
    """Parse NDJSON lines from subprocess stderr and re-emit into parent logger.

    Each line is expected to be a structlog NDJSON record. Non-JSON lines
    (tracebacks, print statements, Playwright debug noise) are emitted as
    ``subprocess_stderr_raw`` warnings so they remain visible but clearly
    flagged as unstructured.

    All re-emitted events carry ``subprocess=True`` to distinguish them from
    parent-native log entries.
    """
    for line in raw_lines:
        stripped = line.rstrip("\n\r")
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            log.warning("subprocess_stderr_raw", raw=stripped, subprocess=True)
            continue

        event = str(record.pop("event", "subprocess_event"))
        level = str(record.pop("level", "info")).lower()
        # Remove timestamp / logger fields that structlog adds automatically.
        record.pop("timestamp", None)
        record.pop("logger", None)
        # Pop "subprocess" to avoid TypeError: got multiple values for kwarg
        # if the child happened to emit log.info("...", subprocess="...").
        # The parent-set subprocess=True kwarg below is authoritative.
        record.pop("subprocess", None)
        emit = getattr(log, level, log.info)
        emit(event, subprocess=True, **record)


def _start_stderr_forwarder(proc: subprocess.Popen[str]) -> threading.Thread:
    """Spawn a daemon thread that forwards proc.stderr NDJSON to parent logger.

    Daemon=True means the thread dies when the parent process dies — no
    zombie threads. The returned thread is stored on the session so its
    lifetime is clearly owned.
    """

    def _reader() -> None:
        try:
            assert proc.stderr is not None
            for line in iter(proc.stderr.readline, ""):
                _forward_subprocess_stderr_lines([line])
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "subprocess_stderr_forwarder_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                subprocess=True,
            )

    t = threading.Thread(target=_reader, daemon=True, name="webui-subprocess-stderr")
    t.start()
    return t


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

    # Forward any NDJSON events the child emitted to stderr into the parent
    # logger BEFORE processing stdout, so vision_fallback_* / plan_* events
    # are visible even when the flow succeeds.
    if proc.stderr:
        _forward_subprocess_stderr_lines(proc.stderr.splitlines())

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


# ---------------------------------------------------------------------------
# Phase 4 — long-lived session helper.
# ---------------------------------------------------------------------------

# Per-op stdin/stdout round-trip timeout.
# Bumped 30 → 90s in chunk 14h-D after live smoke act_20260523_90c146:
# the vision-first path (14g) now does up to TWO Haiku vision calls per
# action when staleness eviction-retry fires (vision_call_1 ≈ 4s + click ≈
# 5s + evict + vision_call_2 ≈ 4s + click_2 ≈ 5s = ~18s minimum; with
# Anthropic latency variance the 30s budget would burst and trigger
# spurious session_not_found cascades). Happy-path describe is still ~1s
# / simple click ~0.5s — 90s only matters for vision-first retry paths.
_SESSION_OP_TIMEOUT_S = 90.0

# Init handshake can be slow — Cisco WebUI login is ~5-20 s on real hardware.
_SESSION_INIT_TIMEOUT_S = 60.0


class WebUISession:
    """Long-lived Playwright child process for AI-driven WebUI configuration.

    Pairs with `_playwright_subprocess._run_session_loop`. The session is
    started lazily on `__init__`, completes login once, then accepts
    JSON-line ops via `send(op_dict)` until `close()` is called.

    Lifetime is intended for ONE planner turn. The parent's 120 s subprocess
    watchdog is the absolute backstop; the per-op timeout above bounds any
    single op.

    Why a thread for the read: `subprocess.Popen.stdout.readline()` is a
    blocking syscall on Windows with no per-call timeout. We do the readline
    on a daemon thread and join with a timeout — on overrun we kill the
    child to unblock the thread, then the session is unusable.
    """

    def __init__(self, action_id: str, *, headless: bool | None = None) -> None:
        self.action_id = action_id
        self._proc: subprocess.Popen[str] | None = None
        self.evidence_dir: str | None = None
        self._stderr_forwarder: threading.Thread | None = None
        self._start(headless)

    def _start(self, headless: bool | None) -> None:
        log.info("webui_session_starting", action_id=self.action_id, headless=headless)
        # bufsize=1 = line-buffered. stderr=PIPE so we can forward NDJSON
        # events (vision_fallback_*, plan_*, etc.) from the child into the
        # parent's logger via _start_stderr_forwarder.
        self._proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "backend.webui_agent._playwright_subprocess"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stderr_forwarder = _start_stderr_forwarder(self._proc)
        # Send the init handshake.
        self._write_line(
            {
                "mode": "session",
                "action_id": self.action_id,
                "headless": headless,
            }
        )
        ready = self._read_line(timeout_s=_SESSION_INIT_TIMEOUT_S)
        if not ready.get("ok") or not ready.get("ready"):
            self.close()
            raise SubprocessFlowError(
                flow="webui_session_init",
                error=str(ready.get("error", "session failed to initialise")),
                exc_type=str(ready.get("exc_type", "InitFailed")),
                stderr="",
            )
        self.evidence_dir = ready.get("evidence_dir")
        log.info(
            "webui_session_ready",
            action_id=self.action_id,
            evidence_dir=self.evidence_dir,
        )

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def send(
        self,
        op_dict: dict[str, Any],
        *,
        timeout_s: float = _SESSION_OP_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Send one op JSON-line, read one JSON-line reply.

        Raises:
            SubprocessFlowError: subprocess died, timed out, or replied
                with unparseable output.
        """
        if not self.is_alive():
            raise SubprocessFlowError(
                flow="webui_session",
                error="subprocess is not alive",
                exc_type="ProcessGone",
                stderr="",
            )
        self._write_line(op_dict)
        return self._read_line(timeout_s=timeout_s)

    def close(self) -> None:
        """Send shutdown, wait briefly, then kill.

        Safe to call multiple times; idempotent.
        """
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(json.dumps({"op": "shutdown"}) + "\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                except (OSError, ValueError, BrokenPipeError):
                    # Child already died or pipe broken — fall through to kill.
                    pass
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    # Wedged even after kill — nothing more to do. Keep
                    # close() idempotent and non-raising.
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=2.0)
        finally:
            log.info("webui_session_closed", action_id=self.action_id)

    def __enter__(self) -> WebUISession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- internals ----

    def _write_line(self, payload: dict[str, Any]) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            self._proc = None
            raise SubprocessFlowError(
                flow="webui_session",
                error=f"failed to write to child stdin: {exc!s}",
                exc_type="BrokenPipe",
                stderr="",
            ) from exc

    def _read_line(self, *, timeout_s: float) -> dict[str, Any]:
        """Read one JSON line with a per-call timeout (thread-based)."""
        assert self._proc is not None
        assert self._proc.stdout is not None
        proc = self._proc
        result: list[str | None] = [None]
        reader_error: list[BaseException | None] = [None]

        def _reader() -> None:
            try:
                assert proc.stdout is not None
                result[0] = proc.stdout.readline()
            except Exception as exc:  # noqa: BLE001 — surface, don't mask as EOF
                reader_error[0] = exc
                result[0] = ""

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            # Reader thread is stuck on readline — child either hung or never
            # wrote. Kill the child so the next caller gets a clean error
            # instead of timing out again.
            proc.kill()
            self._proc = None
            raise SubprocessFlowError(
                flow="webui_session",
                error=f"timed out after {timeout_s}s waiting for child reply",
                exc_type="Timeout",
                stderr="",
            )

        if reader_error[0] is not None:
            # A non-EOF error in the reader thread (previously swallowed and
            # misreported as UnexpectedEOF). Surface the real cause.
            self._proc = None
            raise SubprocessFlowError(
                flow="webui_session",
                error=f"reader thread failed: {reader_error[0]!s}",
                exc_type=type(reader_error[0]).__name__,
                stderr="",
            )

        line = result[0]
        if not line:
            self._proc = None
            raise SubprocessFlowError(
                flow="webui_session",
                error="subprocess closed stdout unexpectedly (no reply)",
                exc_type="UnexpectedEOF",
                stderr="",
            )
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise SubprocessFlowError(
                flow="webui_session",
                error=f"invalid JSON from child: {exc!s}",
                exc_type="JSONDecodeError",
                stderr="",
            ) from exc
