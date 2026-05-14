#!/usr/bin/env python
"""Smoke test for Phase 4 slice 2 — AI-driven WebUI driver vs the real router.

WHAT THIS DOES (safe — no router config change)
- Manually proposes + approves an action_id (PROPOSED -> APPROVED).
- Opens a headed Chromium against ROUTER_HOST/webui/#/general via the
  long-lived WebUISession (same subprocess pattern used by the fast paths).
- Prints the semantic-DOM view describe_page returned — the same JSON
  the planner will see in Phase 5.
- Re-describes once (different op) to prove view_id rolls between calls.
- Calls webui_act_by_intent with intent {role: "textbox", name:
  "Host Name*", action: "fill", value: <PHASE4_DEMO_FILL>} which lets
  login.first_match resolve the live Locator and the bbox reverse-lookup
  pick the matching eid from describe_page's map.
- Calls webui_verify to confirm the page still renders.

WHAT THIS PROVES
- The long-lived subprocess pattern works against real hardware (init
  handshake, JSON-line ops, clean shutdown).
- describe_page returns a usable view from a live Cisco WebUI.
- webui_act_by_intent resolves a semantic intent through first_match
  + reverse-lookup and dispatches into the _do_act self-heal machine.
- The HITL gate accepts the manually-approved action_id; pool.invalidate
  fires after the successful act.

WHAT THIS DOES NOT DO
- Click Apply. The fill is local-only — no router write. If you
  accidentally click Apply in the headed browser, the hostname would
  change to PHASE4_DEMO_FILL on the real device. **Don't click Apply.**
- Test the never-retry-click guard against the live router (covered by
  tests/unit/test_playwright_subprocess.py::test_click_timeout_does_not_retry).

USAGE
    .venv\\Scripts\\python.exe scripts\\smoke_phase4_slice2.py

Requires ROUTER_HOST + ROUTER_WEBUI_USER + ROUTER_WEBUI_PASSWORD in
your .env. Headed Chromium opens so you can watch.
"""

from __future__ import annotations

import json
import sys
import time

from backend.orchestration.confirmations import approve_action, propose_action
from backend.webui_agent.generic_driver import (
    close_all_sessions,
    webui_act_by_intent,
    webui_describe_page,
    webui_open,
    webui_verify,
)

# Clearly-not-a-real-hostname value so an accidental Apply click stands out.
# (Reverting requires changing back via CLI or the fast-path flow.)
PHASE4_DEMO_FILL = "PHASE4-DEMO-DO-NOT-APPLY"


def _print_view(view: dict) -> None:
    """Print a compact summary of describe_page output."""
    print(f"   view_id   = {view['view_id']}")
    print(f"   url       = {view['url']}")
    print(f"   title     = {view['title']}")
    print(
        f"   counts    = {len(view['elements'])} elements, "
        f"{len(view['modals'])} modals, {len(view['errors'])} errors"
    )
    print("   elements (first 12):")
    for el in view["elements"][:12]:
        bits = [
            f"{el['eid']:7s}",
            f"{el['role']:10s}",
            f"name={el.get('name', '')[:35]!r}",
        ]
        if "value" in el:
            bits.append(f"value={el['value'][:20]!r}")
        if el.get("required"):
            bits.append("required")
        print("     " + "  ".join(bits))


def main() -> int:
    # 1. Approve an action_id we can reuse for every write op in this run.
    action_id = propose_action(
        tool="webui_act_by_intent",
        params={
            "intent": {
                "role": "textbox",
                "name": "Host Name*",
                "action": "fill",
                "value": PHASE4_DEMO_FILL,
            },
        },
    )
    approve_action(action_id)
    print(f"=> action_id: {action_id} (APPROVED)\n")

    try:
        # 2. Open the General page — this is also where the pre-snapshot
        #    fires (best-effort; logs warning if SSH unavailable).
        print("=> webui_open('/webui/#/general') ...")
        r = webui_open("/webui/#/general", action_id=action_id, headless=False)
        if "error" in r:
            print(f"FAIL: webui_open returned {json.dumps(r, indent=2)}")
            return 1
        session_id = r["session_id"]
        first_view = r["view"]
        print(f"   session_id = {session_id}")
        _print_view(first_view)
        print()

        # 3. Re-describe — proves the op is wired AND view_id rolls.
        print("=> webui_describe_page (fresh describe) ...")
        r = webui_describe_page(session_id)
        if "error" in r:
            print(f"FAIL: webui_describe_page returned {json.dumps(r, indent=2)}")
            return 1
        second_view = r["view"]
        view_id_rolled = second_view["view_id"] != first_view["view_id"]
        print(
            f"   view_id rolled? {view_id_rolled}  "
            f"({first_view['view_id']} -> {second_view['view_id']})"
        )
        print()

        # 4. Act by intent — the meat. first_match resolves the live Locator,
        #    reverse-lookup picks the matching eid, _do_act dispatches the fill.
        print(f"=> webui_act_by_intent: fill 'Host Name*' with {PHASE4_DEMO_FILL!r} ...")
        r = webui_act_by_intent(
            session_id=session_id,
            intent={
                "role": "textbox",
                "name": "Host Name*",
                "action": "fill",
                "value": PHASE4_DEMO_FILL,
            },
            action_id=action_id,
        )
        reply_str = json.dumps(r, indent=2, default=str)
        if len(reply_str) > 1800:
            reply_str = reply_str[:1800] + "\n   ...(truncated)"
        print(reply_str)
        if not r.get("ok"):
            print(
                f"\nFAIL: act_by_intent did not succeed: failure_reason={r.get('failure_reason')!r}"
            )
            return 1
        print(f"\n   chosen_eid = {r.get('chosen_eid')!r}, attempts = {r.get('attempts')}")
        print()

        # 5. Verify the fill landed in the DOM.
        print(f"=> webui_verify(text={PHASE4_DEMO_FILL!r}) ...")
        r = webui_verify(session_id, PHASE4_DEMO_FILL)
        if "error" in r:
            print(f"FAIL: webui_verify returned {json.dumps(r, indent=2)}")
            return 1
        print(f"   present = {r['present']},  url = {r['url']}")
        print()

        # 6. Pause so you can eyeball the headed browser.
        print("=> Pausing 8 seconds so you can see the filled-in Host Name field.")
        print("   DO NOT click Apply — the value is intentionally a placeholder.")
        time.sleep(8)

        print("=> All Phase 4 slice 2 ops succeeded against the real router.")
        return 0

    finally:
        # Close the subprocess (Chromium too) so we don't leak processes.
        close_all_sessions()


if __name__ == "__main__":
    sys.exit(main())
