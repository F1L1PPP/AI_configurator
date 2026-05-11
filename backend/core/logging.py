import contextlib
import logging
import sys
from pathlib import Path

import structlog

_REDACT_KEYS = frozenset({"password", "secret", "api_key", "token"})


def redact_secrets(logger: object, method: str, event_dict: dict) -> dict:
    for key in _REDACT_KEYS:
        if key in event_dict:
            event_dict[key] = "***REDACTED***"
    return event_dict


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

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    root = logging.getLogger()
    # uvicorn --reload re-runs the FastAPI lifespan on every file save, which
    # would call configure_logging() again and stack a fresh pair of handlers
    # on top of the existing ones — every log line then writes 2x, 3x, … N
    # times. Close + remove any handlers we previously installed before adding
    # the new pair, so the call is idempotent.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        # contextlib.suppress(...) is a "swallow this exception type silently"
        # context manager — same as try/except/pass but one line. handler.close()
        # can raise if the handler's already closed; we don't care, just move on.
        with contextlib.suppress(Exception):
            handler.close()

    root.setLevel(log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
