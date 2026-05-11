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
    root.setLevel(log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
