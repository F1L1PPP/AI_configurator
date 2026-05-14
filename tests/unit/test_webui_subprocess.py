"""Unit tests for WebUISession (parent-side handle for the Phase 4 session subprocess).

Patches `subprocess.Popen` so no real Chromium / Playwright runs. The
session's protocol — init handshake, send/recv JSON lines, clean close
— is exercised against a MagicMock subprocess.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.webui_agent._subprocess import SubprocessFlowError, WebUISession

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
