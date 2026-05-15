import contextlib
import logging
import re
import sys
from pathlib import Path

import structlog

_REDACT_KEYS = frozenset({"password", "secret", "api_key", "token"})

# Keys whose values contain intentional newlines (rendered tracebacks) —
# skip control-char stripping for these so tracebacks stay readable.
_SAFE_KEYS = frozenset({"exception"})

# Strip NUL, SOH-BS, LF-US (0x00-0x08 and 0x0a-0x1f), and DEL (0x7f).
# 0x09 (tab) is excluded so tab-delimited log lines pass through unchanged.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

# Sentinel attribute we tag onto handlers we install ourselves, so reload
# cleanup only touches our own handlers — not anything installed by uvicorn,
# pytest, FastAPI, or a downstream library.
_OWNED_FLAG = "_cisco_ai_owned"


def redact_secrets(logger: object, method: str, event_dict: dict) -> dict:
    for key in _REDACT_KEYS:
        if key in event_dict:
            event_dict[key] = "***REDACTED***"
    return event_dict


def sanitize_control_chars(logger: object, method: str, event_dict: dict) -> dict:
    """Strip dangerous control characters from string values in the event dict.

    Skips keys in _SAFE_KEYS (e.g. ``exception``) which contain intentional
    newlines from rendered tracebacks. Tab (0x09) is also preserved so
    tab-delimited data passes through unchanged.
    """
    for key, value in event_dict.items():
        if key in _SAFE_KEYS:
            continue
        if isinstance(value, str):
            event_dict[key] = _CONTROL_CHARS_RE.sub("", value)
    return event_dict


def _own(handler: logging.Handler) -> logging.Handler:
    """Tag a handler as one we created; cleanup only removes tagged handlers."""
    setattr(handler, _OWNED_FLAG, True)
    return handler


def _remove_owned_handlers(root: logging.Logger) -> None:
    """Remove handlers we previously installed without disturbing anyone else's.

    Two safety rules:
      1. Only touch handlers carrying our sentinel — don't disturb handlers
         that uvicorn, pytest, or a downstream library installed.
      2. Among our own, only call .close() on FileHandlers. logging.StreamHandler
         delegates close() to its underlying stream, so closing the StreamHandler
         we attached to sys.stderr would close sys.stderr itself — every
         subsequent print() / log would silently fail. removeHandler() unhooks
         it from the logger; the handler object then gets garbage-collected
         without closing the wrapped stream.
    """
    for handler in list(root.handlers):
        if not getattr(handler, _OWNED_FLAG, False):
            continue
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            with contextlib.suppress(Exception):
                handler.close()


def configure_logging(log_level: str = "INFO", logs_dir: Path = Path("logs")) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "actions.log"

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        # Render exc_info=True into a real `exception` field with the full
        # traceback as a string. Without this processor, exc_info appears
        # as the literal bool `true` in the JSON log — the calling site
        # captured the exception but the formatter dropped it on the floor.
        # Diagnosed during the Windows + Playwright NotImplementedError
        # hunt — we couldn't see WHERE in the stack the failure happened
        # until tracebacks were rendered.
        structlog.processors.format_exc_info,
        redact_secrets,
        sanitize_control_chars,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    file_handler = _own(logging.FileHandler(log_file, encoding="utf-8"))
    file_handler.setFormatter(formatter)

    stderr_handler = _own(logging.StreamHandler(sys.stderr))
    stderr_handler.setFormatter(formatter)

    root = logging.getLogger()
    # Idempotency: uvicorn --reload re-runs the FastAPI lifespan on every file
    # save, calling configure_logging() again. Without removal, handlers stack
    # and every log line writes 2x, 3x, … N times after N reloads.
    _remove_owned_handlers(root)

    root.setLevel(log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
