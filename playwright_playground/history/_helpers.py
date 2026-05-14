"""Shared helpers for the playground scripts.

Keeps the per-script files focused on the pattern they demonstrate, not
boilerplate. Mirrors the future shape of `backend/webui_agent/` — a session
directory under artifacts/, screenshots numbered per step, structured prints.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

BASE_URL = "http://localhost:8765"
PLAYGROUND_ROOT = Path(__file__).parent.parent
ARTIFACTS_ROOT = PLAYGROUND_ROOT / "artifacts"


def new_session_dir(prefix: str) -> Path:
    """Create artifacts/<prefix>_<timestamp>/ and return its path."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = ARTIFACTS_ROOT / f"{prefix}_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Step:
    """Numbered-screenshot helper. Mirrors how the real WebUI flows will work.

    Usage:
        step = Step(session_dir)
        step("01-login", page)
    """

    def __init__(self, session_dir: Path) -> None:
        self.dir = session_dir
        self.n = 0

    def __call__(self, label: str, page: object) -> Path:  # type: ignore[no-untyped-def]
        self.n += 1
        path = self.dir / f"{self.n:02d}-{label}.png"
        page.screenshot(path=str(path), full_page=True)  # type: ignore[attr-defined]
        print(f"  -> screenshot {path.name}")
        return path
