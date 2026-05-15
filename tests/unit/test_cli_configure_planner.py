"""Unit tests for backend.orchestration.cli_configure_planner."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.orchestration.cli_configure_planner import (
    _INNER_SYSTEM_PROMPT,
    _PLANNER_MODEL,
    _RUNNING_CONFIG_MAX_CHARS,
    draft_cli_plan,
)


def _make_mock_client(text: str) -> MagicMock:
    """Build a mock Anthropic client whose messages.create returns `text`."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text=text)]
    mock_client.messages.create.return_value = mock_response
    return mock_client


def test_draft_cli_plan_happy_path():
    """Inner LLM returns well-formed JSON → structured dict back to caller."""
    payload = {
        "config_commands": [
            "router ospf 100",
            "network 10.0.0.0 0.255.255.255 area 0",
            "exit",
        ],
        "verify_command": "show ip ospf | include 100",
        "verify_pattern": r'Routing Process "ospf 100"',
        "risk": "Adds OSPF process 100; revertible via 'no router ospf 100'.",
    }
    client = _make_mock_client(json.dumps(payload))

    result = draft_cli_plan(
        intent="Configure OSPF process 100 area 0",
        rag_chunks=[{"text": "OSPF config reference", "source": "ospf.pdf", "section": "OSPF"}],
        running_config="hostname LAB\n!\nend",
        client=client,
    )

    assert result["config_commands"] == payload["config_commands"]
    assert result["verify_command"] == payload["verify_command"]
    assert result["verify_pattern"] == payload["verify_pattern"]
    assert "OSPF process 100" in result["risk"]


def test_draft_cli_plan_refuse_non_cli_intent():
    """Inner LLM returns empty config_commands → passed through as refusal."""
    payload = {
        "config_commands": [],
        "verify_command": "",
        "verify_pattern": "",
        "risk": "Intent not a CLI configuration task — read query.",
    }
    client = _make_mock_client(json.dumps(payload))

    result = draft_cli_plan(
        intent="what's the uptime",
        rag_chunks=[],
        running_config="",
        client=client,
    )

    assert result["config_commands"] == []
    assert "read query" in result["risk"]


def test_draft_cli_plan_raises_on_non_json():
    client = _make_mock_client("Sorry I cannot do that.")
    with pytest.raises(RuntimeError, match="non-JSON"):
        draft_cli_plan(
            intent="x",
            rag_chunks=[],
            running_config="",
            client=client,
        )


def test_draft_cli_plan_raises_on_missing_config_commands():
    client = _make_mock_client(json.dumps({"verify_command": "show x"}))
    with pytest.raises(RuntimeError, match="missing 'config_commands'"):
        draft_cli_plan(
            intent="x",
            rag_chunks=[],
            running_config="",
            client=client,
        )


def test_draft_cli_plan_raises_on_non_list_commands():
    """config_commands present but not a list → RuntimeError."""
    client = _make_mock_client(json.dumps({"config_commands": "router ospf 100"}))
    with pytest.raises(RuntimeError, match="not a list"):
        draft_cli_plan(
            intent="x",
            rag_chunks=[],
            running_config="",
            client=client,
        )


def test_draft_cli_plan_recovers_from_prose_wrapped_json():
    """Haiku narrates around the JSON → brace-balanced extractor recovers."""
    payload = {
        "config_commands": ["hostname FOO"],
        "verify_command": "show running-config | include hostname",
        "verify_pattern": "hostname FOO",
        "risk": "Renames the router.",
    }
    prose = f"Here is the plan:\n{json.dumps(payload)}\nHope that helps."
    client = _make_mock_client(prose)

    result = draft_cli_plan(
        intent="rename to FOO",
        rag_chunks=[],
        running_config="",
        client=client,
    )

    assert result["config_commands"] == ["hostname FOO"]


def test_draft_cli_plan_truncates_running_config():
    """Running configs > _RUNNING_CONFIG_MAX_CHARS get truncated (from the
    END) so the prompt doesn't blow up. Structural blocks (top of config)
    survive; dynamic dynamic tail (boot info, certificates) gets dropped.
    """
    huge_rc = "interface GigabitEthernet0/0/1\n ip address 10.0.0.1 255.255.255.0\n"
    huge_rc += "x\n" * _RUNNING_CONFIG_MAX_CHARS  # well over the cap
    payload = {
        "config_commands": ["no shutdown"],
        "verify_command": "show ip interface brief",
        "verify_pattern": "GigabitEthernet0/0/1",
        "risk": "low",
    }
    client = _make_mock_client(json.dumps(payload))

    draft_cli_plan(
        intent="bring interface up",
        rag_chunks=[],
        running_config=huge_rc,
        client=client,
    )

    sent = client.messages.create.call_args.kwargs["messages"][0]["content"]
    # Truncation marker present, full original is NOT
    assert "[truncated]" in sent
    assert len(sent) < len(huge_rc) + 5000  # sanity: didn't somehow include the full huge rc


# ---------------------------------------------------------------------------
# Static asserts on prompt content + model lock
# ---------------------------------------------------------------------------


def test_planner_model_is_haiku():
    """Production rule: only claude-haiku-4-5-20251001 in backend LLM calls."""
    assert _PLANNER_MODEL == "claude-haiku-4-5-20251001"


def test_inner_prompt_forbids_destructive_commands():
    """Prompt must instruct the LLM not to emit destructive commands. The
    server-side denylist is the actual gate, but reinforcing it in the
    prompt reduces refusal-loop churn."""
    for forbidden in ("reload", "erase", "delete", "format", "write erase"):
        assert forbidden in _INNER_SYSTEM_PROMPT.lower()


def test_inner_prompt_requires_show_verify_command():
    """verify_command must start with `show ` — prompt says so."""
    assert "must start with `show `" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_documents_refusal_shape():
    """Refusal case (non-CLI intent) must be documented so the LLM knows
    to emit empty config_commands instead of hallucinating."""
    assert "Intent not a CLI configuration task" in _INNER_SYSTEM_PROMPT


def test_inner_prompt_warns_about_doc_chunks():
    """Prompt-injection defense — doc chunks are reference, not instructions."""
    assert "reference material" in _INNER_SYSTEM_PROMPT
    assert "never an" in _INNER_SYSTEM_PROMPT.lower() or "never an" in _INNER_SYSTEM_PROMPT
