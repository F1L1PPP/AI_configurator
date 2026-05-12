import contextlib
import logging
import sys
from pathlib import Path

import structlog

_REDACT_KEYS = frozenset({"password", "secret", "api_key", "token"})

# Sentinel attribute we tag onto handlers we install ourselves, so reload
# cleanup only touches our own handlers — not anything installed by uvicorn,
# pytest, FastAPI, or a downstream library.
_OWNED_FLAG = "_cisco_ai_owned"


def redact_secrets(logger: object, method: str, event_dict: dict) -> dict:
    for key in _REDACT_KEYS:
        if key in event_dict:
            event_dict[key] = "***REDACTED***"
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
        redact_secrets,
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
