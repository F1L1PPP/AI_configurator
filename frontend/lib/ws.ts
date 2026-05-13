// Typed client for GET /ws/agent.
//
// The backend (backend/api/routes_ws.py) forwards every event published on
// backend.core.eventbus.bus. The discriminated union below mirrors the
// `type` values emitted by backend/orchestration/planner.py:_emit.

import { WS_BASE } from "./api";

export type AgentEvent =
  | { type: "agent_thinking"; ts: string; data: { iteration: number; model: string } }
  | { type: "tool_call"; ts: string; data: { name: string; input: Record<string, unknown>; id: string } }
  | { type: "tool_result"; ts: string; data: { name: string; result: Record<string, unknown> } }
  | { type: "awaiting_approval"; ts: string; data: { action_id: string; preview?: string } }
  | { type: "applied"; ts: string; data: { tool: string; summary?: string; snapshot_post?: string; duration_ms?: number } }
  | { type: "verified"; ts: string; data: { tool?: string; action_id?: string } }
  | { type: "error"; ts: string; data: { message: string; max?: number } };

export type AgentSource = {
  source: string;
  section: string;
  text: string;
  score: number;
};

function isAgentSource(value: unknown): value is AgentSource {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.source === "string" &&
    typeof v.section === "string" &&
    typeof v.text === "string" &&
    typeof v.score === "number"
  );
}

// Extract Cisco doc citations from an event trace. A reply's sources are
// every chunk returned by every search_docs tool_result in the same turn.
// Runtime validation per result (audit #15-style cast-and-pray was the
// last copy of that anti-pattern in this module).
export function extractSources(events: AgentEvent[]): AgentSource[] {
  const seen = new Map<string, AgentSource>();
  for (const ev of events) {
    if (ev.type !== "tool_result") continue;
    if (ev.data.name !== "search_docs") continue;
    const rawResults = (ev.data.result as { results?: unknown }).results;
    if (!Array.isArray(rawResults)) continue;
    for (const r of rawResults) {
      if (!isAgentSource(r)) continue;
      const key = `${r.source}::${r.section}`;
      if (!seen.has(key)) seen.set(key, r);
    }
  }
  return Array.from(seen.values());
}

export type WsHandle = {
  close: () => void;
};

// Open a WS to /ws/agent with automatic reconnect-on-close (exponential
// backoff 500ms -> 10s). Returns a handle whose close() permanently tears
// down the connection and stops further reconnect attempts.
//
// Why reconnect: if the backend restarts mid-session (or uvicorn reloads
// after a code change) we'd otherwise silently stop receiving events.
// onStatus("closed") still fires between reconnects so the UI dot reflects
// reality.
//
// Reconnect cap: after MAX_RECONNECT_ATTEMPTS consecutive failures, stop
// trying and call onStatus("error"). Prevents an infinite-reconnect loop
// against a backend that intentionally refuses the client (auth fails,
// origin not allowed, subscriber cap reached). The counter resets on
// every successful open.
export function connectAgentWs(
  onEvent: (event: AgentEvent) => void,
  onStatus?: (status: "open" | "closed" | "error") => void,
): WsHandle {
  const MAX_BACKOFF_MS = 10_000;
  const MAX_RECONNECT_ATTEMPTS = 20;
  let ws: WebSocket | null = null;
  let closedByCaller = false;
  let backoffMs = 500;
  let consecutiveFailures = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function open(): void {
    if (closedByCaller) return;
    ws = new WebSocket(`${WS_BASE}/ws/agent`);
    ws.onopen = () => {
      backoffMs = 500;
      consecutiveFailures = 0;
      onStatus?.("open");
    };
    ws.onclose = () => {
      onStatus?.("closed");
      if (closedByCaller) return;
      consecutiveFailures += 1;
      if (consecutiveFailures >= MAX_RECONNECT_ATTEMPTS) {
        // Don't loop forever against a backend that keeps rejecting us.
        // Surface as a hard error so the UI can show a meaningful state.
        // eslint-disable-next-line no-console
        console.warn(
          `[ws/agent] gave up after ${MAX_RECONNECT_ATTEMPTS} reconnect attempts`,
        );
        onStatus?.("error");
        return;
      }
      reconnectTimer = setTimeout(open, backoffMs);
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    };
    ws.onerror = () => onStatus?.("error");
    ws.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data) as AgentEvent;
        onEvent(ev);
      } catch (err) {
        // Surface to the console — the backend only sends JSON, so this
        // means an event-type mismatch or a corrupted frame worth knowing.
        // eslint-disable-next-line no-console
        console.warn("[ws/agent] failed to parse frame:", err, msg.data);
      }
    };
  }

  open();
  return {
    close: () => {
      closedByCaller = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      ws?.close();
    },
  };
}
