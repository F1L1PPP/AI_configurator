"""GET /api/logs/recent — last N lines from the structured action log."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query

from backend.core.settings import get_settings

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs/recent")
async def get_recent_logs(
    limit: int = Query(default=20, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Return the most recent *limit* entries from logs/actions.log.

    Each entry is a parsed JSONL object. Lines that fail JSON parsing are
    silently skipped (structlog startup messages may not be valid JSONL).
    Results are newest-first.
    """
    log_file = get_settings().logs_dir / "actions.log"
    if not log_file.exists():
        return []

    lines = log_file.read_text(encoding="utf-8").splitlines()
    tail = lines[-limit:] if len(lines) > limit else lines

    entries: list[dict[str, Any]] = []
    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return entries
