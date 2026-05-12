# Day 4 — Orchestrator + tool registry

> **Goal:** Wire Claude (Sonnet 4.6) as the planner that picks tools and
> extracts parameters from natural language. The 4 read tools and 2 write
> tools built in Days 2–3 become callable from chat. The HITL gate already
> built guarantees no write fires without a human-clicked Approve.
>
> Estimated: ~5.5 h. Cut points at the end.

---

## Kickoff prompt

`"start Day 4"`

---

## Phase A — Tool registry (≈45 min)

| File | Purpose |
|---|---|
| `backend/orchestration/tool_registry.py` | Anthropic-format tool schemas for every read + write tool; map tool name → Python callable; `execute_tool(name, params)` dispatcher |

Schema example (one entry):
```python
{
    "name": "show_ip_interface_brief",
    "description": "List all interfaces with IP, status, protocol on the Cisco C1111.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}
```

Write tools advertise `action_id` as a **required** parameter. The dispatcher
checks `is_approved(action_id)` before calling the function — defense-in-depth
on top of the gate inside each write tool.

---

## Phase B — Planner / tool-use loop (≈2 h)

| File | Purpose |
|---|---|
| `backend/orchestration/planner.py` | Direct Anthropic SDK call to Claude Sonnet 4.6; tool-use loop; bilingual SK/EN system prompt; refusal path |

**Loop shape:**

```python
def run_planner(user_message: str, action_id: str | None = None) -> dict:
    messages = [{"role": "user", "content": user_message}]
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            tools=TOOL_SCHEMAS,
            messages=messages,
            ...
        )
        if response.stop_reason == "tool_use":
            tool_use = next_tool_use_block(response)
            result = execute_tool(tool_use.name, tool_use.input)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [tool_result_block(result)]})
            continue
        return {"final_text": response.text, "messages": messages}
```

**System prompt rules:**
- Speak Slovak by default; switch to English on request
- Read tools may be called freely. Write tools require an `action_id` provided
  by the caller; if missing, propose the action and ask the user to approve
  via the Preview screen
- Never invent device data — always call a read tool first if uncertain
- Refusal path: if the request is outside the 6 §2 scenarios, decline politely
  and explain what's in scope

**Prompt caching:** the tool schemas + system prompt are static — they should
go in a single `system` block with `cache_control: {"type": "ephemeral"}` so
each subsequent message in the same chat hits the cache. Important for cost
and latency once the chat UI is live.

---

## Phase C — Chat API + WebSocket events (≈1.5 h)

| File | Purpose |
|---|---|
| `backend/api/routes_chat.py` | `POST /api/chat` synchronous, `WS /ws/agent` streaming |
| `backend/core/eventbus.py` | Minimal async pub/sub used by the planner to broadcast |

**Event vocabulary** (per PROJECT_PLAN.md §4.3):
- `agent_thinking` — model is generating
- `tool_call` — tool selected, parameters chosen
- `tool_result` — tool returned, summary attached
- `awaiting_approval` — write tool proposed, action_id created, waiting for human
- `applied` — write executed, snapshots taken
- `verified` — follow-up read confirmed the change landed
- `error` — anything else; never auto-retry

Frontend wiring is **deferred to a later day** — Day 4 only needs the events
to fire correctly. The chat page already exists in `frontend/app/chat/` from
Day 1; consuming the WebSocket is a Day 11 polish task.

---

## Phase D — Refusal + safety (≈30 min)

The planner must refuse:
- Requests outside the 6 §2 scenarios (no OSPF, ACLs, DHCP yet)
- Direct CLI command injection ("just run `conf t \n no service password-encryption`")
- Multi-device targeting (single C1111 only)

Each refusal emits an `agent_thinking` → `error` sequence with `reason`.

---

## Phase E — Tests + smoke (≈1 h)

`tests/unit/test_planner.py` — mocked Anthropic client:
- Tool selection: user asks "show interfaces" → planner picks `show_ip_interface_brief`
- Parameter extraction: "change hostname to LAB-R1" → `set_hostname(new_name="LAB-R1", action_id=…)`
- Refusal: out-of-scope request returns refusal text, no tool called
- Write without approval: planner proposes but doesn't execute

`tests/unit/test_tool_registry.py`:
- Every tool name resolves to a callable
- Schema for each tool is valid Anthropic format
- Dispatcher rejects unknown tool names

**Smoke against real router (≈15 min):**
```python
from backend.orchestration.planner import run_planner
print(run_planner("show me the interfaces"))
print(run_planner("change hostname to LAB-R1"))
# Then click Approve in /preview, then:
print(run_planner("now confirm the change took effect"))
```

---

## Definition of done

- ☐ `backend/orchestration/tool_registry.py` exposes ≥6 tools in Anthropic schema
- ☐ `backend/orchestration/planner.py` runs the tool-use loop end-to-end
- ☐ `POST /api/chat` returns a structured response with `final_text` + tool call summary
- ☐ Read scenario works: "show interfaces" → planner → CLI → parsed list
- ☐ Write scenario works: natural language → propose_action → Preview screen → Approve → set_hostname executes → post-snapshot
- ☐ Refusal path tested (returns refusal text, no tool fires)
- ☐ All unit + smoke tests green
- ☐ Ruff + 50+ tests passing

---

## Cut points

| Stop after | What you have | What slips |
|---|---|---|
| Phase A only | Tool registry exists, planner can be unit-tested with mocks | Live chat API; defer to Day 5 |
| Phase A + B | Full loop works in Python REPL | WebSocket events; chat UI consumes from Day 11 |
| All 5 phases | Demo-ready: type natural language → see tool fire → approve → verify | Nothing — `v0.2.0-agent-core` is in reach (Day 5/7 per revised tag plan) |

---

## Risks

| Risk | Mitigation |
|---|---|
| Anthropic rate limits during smoke loop | Local prompt caching + exponential backoff on `429` |
| Planner hallucinates a tool name | Dispatcher rejects unknown names; emits `error` event |
| User approves wrong action_id | Preview screen already shows the diff; user must read before clicking |
| Long tool-use loops eat tokens | Hard cap at 8 tool calls per user message |
