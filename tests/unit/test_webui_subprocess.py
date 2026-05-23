"""Unit tests for WebUISession and subprocess stderr forwarding helpers.

Patches `subprocess.Popen` so no real Chromium / Playwright runs. The
session's protocol — init handshake, send/recv JSON lines, clean close
— is exercised against a MagicMock subprocess.

Also unit-tests `_forward_subprocess_stderr_lines` in isolation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.webui_agent._subprocess import (
    SubprocessFlowError,
    WebUISession,
    _forward_subprocess_stderr_lines,
)

pytestmark = pytest.mark.webui


def _make_fake_proc(readline_returns: list[str]) -> MagicMock:
    """Return a MagicMock that quacks like a live subprocess.Popen.

    `readline_returns` is the queued list of JSON-line strings the child
    will "write" to stdout. Each `proc.stdout.readline()` call pops one.
    """
    proc = MagicMock()
    proc.poll.return_value = None  # alive
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline.side_effect = readline_returns
    return proc


def test_session_init_sends_handshake_and_reads_ready():
    with patch("backend.webui_agent._subprocess.subprocess.Popen") as mock_popen:
        fake_proc = _make_fake_proc(
            readline_returns=[json.dumps({"ok": True, "ready": True, "evidence_dir": "/tmp/evid"})]
        )
        mock_popen.return_value = fake_proc

        sess = WebUISession("act_001", headless=True)

        # Init handshake JSON should have been written + flushed.
        write_calls = fake_proc.stdin.write.call_args_list
        assert len(write_calls) == 1
        init_line = write_calls[0].args[0]
        init = json.loads(init_line)
        assert init["mode"] == "session"
        assert init["action_id"] == "act_001"
        assert init["headless"] is True
        fake_proc.stdin.flush.assert_called()

        assert sess.evidence_dir == "/tmp/evid"
        assert sess.is_alive() is True

        sess.close()


def test_session_init_failure_raises_subprocess_flow_error():
    with patch("backend.webui_agent._subprocess.subprocess.Popen") as mock_popen:
        fake_proc = _make_fake_proc(
            readline_returns=[
                json.dumps(
                    {
                        "ok": False,
                        "error": "WebUI login failed",
                        "exc_type": "RuntimeError",
                    }
                )
            ]
        )
        mock_popen.return_value = fake_proc

        with pytest.raises(SubprocessFlowError) as excinfo:
            WebUISession("act_002")

        assert excinfo.value.exc_type == "RuntimeError"
        assert "login" in excinfo.value.error.lower()


def test_send_writes_op_and_returns_parsed_reply():
    with patch("backend.webui_agent._subprocess.subprocess.Popen") as mock_popen:
        fake_proc = _make_fake_proc(
            readline_returns=[
                json.dumps({"ok": True, "ready": True, "evidence_dir": "/tmp/x"}),
                json.dumps(
                    {
                        "ok": True,
                        "view": {"view_id": "abc12345", "elements": []},
                    }
                ),
            ]
        )
        mock_popen.return_value = fake_proc

        sess = WebUISession("act_003")
        reply = sess.send({"op": "open", "path": "/webui/#/general"})

        # The second stdin.write call (after the init handshake) is the op.
        write_calls = fake_proc.stdin.write.call_args_list
        assert len(write_calls) == 2
        op_line = write_calls[1].args[0]
        op = json.loads(op_line)
        assert op == {"op": "open", "path": "/webui/#/general"}

        assert reply == {
            "ok": True,
            "view": {"view_id": "abc12345", "elements": []},
        }


def test_send_after_close_raises_process_gone():
    with patch("backend.webui_agent._subprocess.subprocess.Popen") as mock_popen:
        fake_proc = _make_fake_proc(
            readline_returns=[json.dumps({"ok": True, "ready": True, "evidence_dir": "/tmp/x"})]
        )
        mock_popen.return_value = fake_proc

        sess = WebUISession("act_004")
        sess.close()

        with pytest.raises(SubprocessFlowError) as excinfo:
            sess.send({"op": "describe"})

        assert excinfo.value.exc_type == "ProcessGone"


def test_close_is_idempotent():
    with patch("backend.webui_agent._subprocess.subprocess.Popen") as mock_popen:
        fake_proc = _make_fake_proc(
            readline_returns=[json.dumps({"ok": True, "ready": True, "evidence_dir": "/tmp/x"})]
        )
        mock_popen.return_value = fake_proc

        sess = WebUISession("act_005")
        sess.close()
        # Second close must not raise.
        sess.close()


def test_unexpected_eof_raises():
    with patch("backend.webui_agent._subprocess.subprocess.Popen") as mock_popen:
        fake_proc = _make_fake_proc(
            readline_returns=[
                json.dumps({"ok": True, "ready": True, "evidence_dir": "/tmp/x"}),
                "",  # EOF on the next read
            ]
        )
        mock_popen.return_value = fake_proc

        sess = WebUISession("act_006")
        with pytest.raises(SubprocessFlowError) as excinfo:
            sess.send({"op": "describe"})

        assert excinfo.value.exc_type == "UnexpectedEOF"
        # Session should have torn itself down after the EOF.
        assert sess.is_alive() is False


def test_invalid_json_reply_raises():
    with patch("backend.webui_agent._subprocess.subprocess.Popen") as mock_popen:
        fake_proc = _make_fake_proc(
            readline_returns=[
                json.dumps({"ok": True, "ready": True, "evidence_dir": "/tmp/x"}),
                "not valid json\n",
            ]
        )
        mock_popen.return_value = fake_proc

        sess = WebUISession("act_007")
        with pytest.raises(SubprocessFlowError) as excinfo:
            sess.send({"op": "describe"})

        assert excinfo.value.exc_type == "JSONDecodeError"


# ---------------------------------------------------------------------------
# Tests for _forward_subprocess_stderr_lines
# ---------------------------------------------------------------------------


def test_forward_subprocess_stderr_lines_emits_json_event_at_correct_level():
    """NDJSON lines are re-emitted via the parent logger at the correct level,
    and all events include subprocess=True."""
    ndjson_line = json.dumps(
        {
            "event": "vision_fallback_resolved",
            "level": "warning",
            "eid": "GigabitEthernet0/0",
            "strategy": "haiku_vision",
        }
    )

    with patch("backend.webui_agent._subprocess.log") as mock_log:
        _forward_subprocess_stderr_lines([ndjson_line])

    mock_log.warning.assert_called_once_with(
        "vision_fallback_resolved",
        subprocess=True,
        eid="GigabitEthernet0/0",
        strategy="haiku_vision",
    )


def test_forward_subprocess_stderr_lines_falls_back_on_non_json():
    """Non-JSON lines (tracebacks, prints) are emitted as subprocess_stderr_raw warnings."""
    traceback_line = "Traceback (most recent call last):"

    with patch("backend.webui_agent._subprocess.log") as mock_log:
        _forward_subprocess_stderr_lines([traceback_line])

    mock_log.warning.assert_called_once_with(
        "subprocess_stderr_raw",
        raw=traceback_line,
        subprocess=True,
    )


def test_forward_subprocess_stderr_lines_skips_empty_lines():
    """Empty strings and bare newlines produce no log calls."""
    with patch("backend.webui_agent._subprocess.log") as mock_log:
        _forward_subprocess_stderr_lines(["", "\n"])

    mock_log.info.assert_not_called()
    mock_log.warning.assert_not_called()
    mock_log.error.assert_not_called()
    mock_log.debug.assert_not_called()


def test_forward_subprocess_stderr_lines_handles_subprocess_kwarg_collision():
    """Audit regression: if child emits a field literally named 'subprocess',
    the parent's subprocess=True kwarg must not collide and raise TypeError.

    Without record.pop('subprocess', None), real subprocess code that does
    log.info('event', subprocess='child_val') would crash the forwarder via
    'got multiple values for keyword argument'.
    """
    line = json.dumps(
        {
            "event": "vision_resolved",
            "level": "info",
            "subprocess": "child_says_so",
            "selector": "input[name='networkIp']",
        }
    )

    with patch("backend.webui_agent._subprocess.log") as mock_log:
        # Must NOT raise TypeError.
        _forward_subprocess_stderr_lines([line])

    # Parent-set subprocess=True wins; child's value dropped.
    mock_log.info.assert_called_once_with(
        "vision_resolved",
        subprocess=True,
        selector="input[name='networkIp']",
    )
