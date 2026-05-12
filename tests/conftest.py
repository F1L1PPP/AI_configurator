"""Shared pytest fixtures for the whole `tests/` tree.

Pytest auto-discovers this file and applies any `autouse=True` fixture to
every test under the directory. Keep this file SMALL — only lift things
that are truly identical across multiple test files. File-specific
fixtures (mocks for particular tools, stubs for particular flows) stay
inside the test file that uses them.

What's here:
- `_clean_actions` (autouse): clears the in-memory confirmations store
  before and after every test. Previously duplicated in
  test_orchestration, test_tool_registry, test_cli_write_tools, and
  test_webui_change_hostname_flow. (Audit #27.)
"""

from __future__ import annotations

import pytest

from backend.orchestration.confirmations import _reset_for_testing


@pytest.fixture(autouse=True)
def _clean_actions() -> None:
    """Reset the in-memory action store before and after each test.

    Tests that propose/approve actions could otherwise leak state into
    other tests via the module-level `_actions` dict.
    """
    _reset_for_testing()
    yield
    _reset_for_testing()
