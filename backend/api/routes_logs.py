"""GET /api/logs/recent — last N lines from the structured action log.

Implementation note: uses a deque(maxlen=N) bounded tail so memory stays
flat regardless of how large logs/actions.log grows. Reading the whole
file with read_text().splitlines() (the old approach) would OOM the
worker once the log hit a few hundred MB.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from fastapi import APIRouter, Query

from backend.core.logging import get_logger
from backend.core.settings import get_settings

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["logs"])

# Hard cap on response size. Keep the query parameter bounded so a caller
# can't force the worker to walk arbitrary amounts of the log file.
_MAX_LIMIT = 200


@router.get("/logs/recent")
async def get_recent_logs(
    limit: int = Query(default=20, ge=1, le=_MAX_LIMIT),
) -> list[dict[str, Any]]:
    """Return the most recent *limit* entries from logs/actions.log.

    Each entry is a parsed JSONL object. Lines that fail JSON parsing are
    logged as warnings (so format drift is visible) and skipped. Results
    are newest-first.
    """
    log_file = get_settings().logs_dir / "actions.log"
    if not log_file.exists():
        return []

    # Stream the file line-by-line; deque(maxlen=N) keeps only the last N
    # in memory. O(file_size) time, O(N) memory.
    with log_file.open("r", encoding="utf-8") as fh:
        tail = deque(fh, maxlen=limit)

    entries: list[dict[str, Any]] = []
    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            # Don't swallow silently — log so we notice format drift.
            log.warning(
                "log_line_invalid_json",
                error=str(exc),
                line_preview=line[:120],
            )
            continue

    return entries
