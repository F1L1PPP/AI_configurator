"""Tests for backend.core.logging.

Two invariants we must never regress:

  1. configure_logging() is idempotent — calling it N times produces the
     same handler count as calling it once. Without this, uvicorn --reload
     stacks duplicate handlers and every log line writes N times.

  2. configure_logging() never closes sys.stderr. logging.StreamHandler.close()
     delegates to its underlying stream's close(); since one of our handlers
     wraps sys.stderr, an incorrect cleanup could close stderr globally and
     break all subsequent output.
"""

import contextlib
import logging
import sys

import pytest

from backend.core.logging import _OWNED_FLAG, configure_logging


def _count_owned(root: logging.Logger) -> int:
    """Count only handlers carrying our sentinel.

    pytest's log-capture plugin installs its own handlers on the root logger,
    so a naive `len(root.handlers)` overcounts. We only care about whether
    OUR cleanup of OUR handlers is correct.
    """
    return sum(1 for h in root.handlers if getattr(h, _OWNED_FLAG, False))


@pytest.fixture
def fresh_root_logger():
    """Snapshot the root logger, restore after the test.

    Logging is global state — without this fixture a failing test would leave
    handlers behind and pollute subsequent tests.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    # Strip everything so each test starts from a known-empty root logger.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    yield root

    # Teardown: any handler currently on root was added during the test.
    # configure_logging() installs a FileHandler against tmp_path; leaving it
    # open holds the file descriptor and can block tmp_path cleanup on Windows.
    # Mirror the production rule: close FileHandlers, NEVER close StreamHandlers
    # (their stream might be sys.stderr / sys.stdout — closing it breaks output
    # globally, same bug the production _remove_owned_handlers avoids).
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            with contextlib.suppress(Exception):
                handler.close()

    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def test_configure_logging_is_idempotent(fresh_root_logger, tmp_path):
    # First call installs exactly two OWNED handlers: one FileHandler + one StreamHandler.
    configure_logging(log_level="INFO", logs_dir=tmp_path)
    first_count = _count_owned(fresh_root_logger)
    assert first_count == 2, f"expected 2 owned handlers after first call, got {first_count}"

    # Subsequent calls must NOT stack additional owned handlers.
    configure_logging(log_level="INFO", logs_dir=tmp_path)
    configure_logging(log_level="INFO", logs_dir=tmp_path)
    configure_logging(log_level="INFO", logs_dir=tmp_path)
    assert _count_owned(fresh_root_logger) == 2, (
        "configure_logging() stacked owned handlers across multiple calls — "
        "would duplicate log lines on every uvicorn reload"
    )


def test_configure_logging_does_not_close_stderr(fresh_root_logger, tmp_path):
    # The dangerous case: calling configure_logging() again removes the previous
    # StreamHandler. logging.StreamHandler.close() would close sys.stderr itself
    # because the handler wraps it. Our cleanup only closes FileHandlers, so
    # stderr must remain writable across many calls.
    for _ in range(5):
        configure_logging(log_level="INFO", logs_dir=tmp_path)

    assert not sys.stderr.closed, "sys.stderr was closed by configure_logging()"
    # Actually exercising the write would crash if stderr is broken.
    sys.stderr.write("")
    sys.stderr.flush()


def test_configure_logging_does_not_touch_foreign_handlers(fresh_root_logger, tmp_path):
    # If uvicorn / pytest / a downstream library installed its own handler on
    # the root logger, our cleanup must leave it alone.
    foreign = logging.StreamHandler(sys.stdout)
    fresh_root_logger.addHandler(foreign)

    configure_logging(log_level="INFO", logs_dir=tmp_path)
    configure_logging(log_level="INFO", logs_dir=tmp_path)

    assert foreign in fresh_root_logger.handlers, (
        "configure_logging() removed a handler it didn't install — should only "
        "clean up handlers carrying our sentinel attribute"
    )
