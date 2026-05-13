"""Run the 6 §2 smoke scenarios end-to-end and report pass/fail.

Wraps pytest with a Day-8-ready summary table:

    SMOKE TEST RESULTS                         2026-05-13 12:34:56
    -------------------------------------------------------------------------------
    Scenario                                                              Status
    -------------------------------------------------------------------------------
    01 -- CLI: show interfaces + show version                               PASS
    02 -- CLI: show running-config                                          PASS
    03 -- CLI: change hostname (round-trip)                                 SKIP
    04 -- RAG: query Cisco docs with citations                              PASS
    05 -- WebUI: change hostname                                            SKIP
    06 -- WebUI: add access VLAN                                            SKIP
    -------------------------------------------------------------------------------
    Result: 3 pass / 0 fail / 3 skip
    Artifacts: artifacts/smoke/2026-05-13_12-34-56/

Usage:

    # Read-only scenarios (1, 2, 4):
    python scripts/run_smoke_tests.py

    # Full set (incl. write scenarios -- mutates the router and restores):
    SMOKE_ALLOW_WRITES=1 python scripts/run_smoke_tests.py

    # WebUI scenarios headless (CI):
    SMOKE_HEADLESS=1 SMOKE_ALLOW_WRITES=1 python scripts/run_smoke_tests.py

Exit code:
    0 -- every scenario that *ran* passed (skipped scenarios don't fail)
    1 -- at least one scenario failed
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent

SCENARIO_DESCRIPTIONS: dict[str, str] = {
    "test_01_cli_read": "01 -- CLI: show interfaces + show version",
    "test_02_cli_show_running_config": "02 -- CLI: show running-config",
    "test_03_cli_set_hostname": "03 -- CLI: change hostname (round-trip)",
    "test_04_rag_query": "04 -- RAG: query Cisco docs with citations",
    "test_05_webui_set_hostname": "05 -- WebUI: change hostname",
    "test_06_webui_add_vlan": "06 -- WebUI: add access VLAN",
}


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _python_exe() -> str:
    """Pick .venv\\Scripts\\python.exe on Windows, .venv/bin/python on POSIX."""
    win = WORKTREE / ".venv" / "Scripts" / "python.exe"
    posix = WORKTREE / ".venv" / "bin" / "python"
    if win.exists():
        return str(win)
    if posix.exists():
        return str(posix)
    return sys.executable


def main() -> int:
    artifacts_dir = WORKTREE / "artifacts" / "smoke" / _ts()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    json_report = artifacts_dir / "results.json"

    cmd = [
        _python_exe(),
        "-m",
        "pytest",
        "tests/smoke/",
        "-v",
        "--tb=short",
        "-p",
        "no:cacheprovider",
        "--json-report",
        f"--json-report-file={json_report}",
    ]

    # Fallback: pytest-json-report may not be installed. Run with a simpler
    # output capture if the plugin is missing.
    proc = subprocess.run(cmd, cwd=str(WORKTREE), capture_output=True, text=True)
    if "unrecognized arguments: --json-report" in proc.stderr:
        cmd = [c for c in cmd if not c.startswith("--json-report")]
        proc = subprocess.run(cmd, cwd=str(WORKTREE), capture_output=True, text=True)

    # Stream pytest's stdout for live visibility
    print(proc.stdout)
    if proc.stderr.strip():
        print("--- stderr ---", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)

    # Parse the JSON report if available; otherwise fall back to a coarse
    # pass/fail from the exit code.
    results: dict[str, str] = {}
    if json_report.exists():
        data = json.loads(json_report.read_text())
        for t in data.get("tests", []):
            nodeid = t["nodeid"]
            outcome = t["outcome"].upper()
            # Bucket each pytest test by its file stem
            for stem in SCENARIO_DESCRIPTIONS:
                if stem in nodeid:
                    # Promote SKIP > PASS > FAIL aggregation per file
                    cur = results.get(stem)
                    if cur is None:
                        results[stem] = outcome
                    elif outcome == "FAILED" or cur == "FAILED":
                        results[stem] = "FAILED"
                    elif outcome == "PASSED":
                        results[stem] = "PASSED"
    else:
        # No JSON report plugin available — fall back to parsing pytest's
        # stdout lines like "tests/smoke/scenarios/test_NN_xxx.py::... PASSED"
        # / "... SKIPPED" / "... FAILED" so we still get per-scenario status.
        for line in proc.stdout.splitlines():
            for stem in SCENARIO_DESCRIPTIONS:
                if stem not in line:
                    continue
                if " PASSED" in line:
                    outcome = "PASSED"
                elif " FAILED" in line:
                    outcome = "FAILED"
                elif " SKIPPED" in line:
                    outcome = "SKIPPED"
                else:
                    continue
                cur = results.get(stem)
                if cur is None or outcome == "FAILED":
                    results[stem] = outcome
                elif cur == "PASSED" and outcome == "SKIPPED":
                    pass  # keep PASSED
                elif cur == "SKIPPED" and outcome == "PASSED":
                    results[stem] = "PASSED"

    # ------------------------------------------------------------------
    # Render summary table
    # ------------------------------------------------------------------
    # ASCII-only output: Windows cp1252 console can't print box-drawing chars.
    width = 79
    bar = "-" * width
    print()
    print("SMOKE TEST RESULTS".ljust(width - 20) + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(bar)
    print(f"{'Scenario':<60}{'Status':>19}")
    print(bar)

    passes = fails = skips = 0
    for stem, desc in SCENARIO_DESCRIPTIONS.items():
        outcome = results.get(stem, "—")
        if outcome == "PASSED":
            tag = "PASS"
            passes += 1
        elif outcome == "FAILED":
            tag = "FAIL"
            fails += 1
        elif outcome == "SKIPPED":
            tag = "SKIP"
            skips += 1
        else:
            tag = outcome or "—"
        print(f"{desc:<60}{tag:>19}")
    print(bar)
    print(f"Result: {passes} pass / {fails} fail / {skips} skip")
    print(f"Artifacts: {artifacts_dir.relative_to(WORKTREE)}")
    print()

    # Persist a compact JSON for downstream tooling
    (artifacts_dir / "summary.json").write_text(
        json.dumps(
            {
                "passes": passes,
                "fails": fails,
                "skips": skips,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
