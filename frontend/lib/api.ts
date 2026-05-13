export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// Same base as API_BASE but with the WS scheme — derived so a single env
// var (NEXT_PUBLIC_API_BASE) controls both HTTP and WS endpoints.
export const WS_BASE = API_BASE.replace(/^http/, "ws");

// Pre-built endpoint URLs — components import these instead of string-
// templating in their JSX (audit #21). One place to update if routes move.
export const endpoints = {
  health: () => `${API_BASE}/healthz`,
  logs: (limit: number) => `${API_BASE}/api/logs/recent?limit=${limit}`,
  approve: (actionId: string) => `${API_BASE}/api/approve/${actionId}`,
  reject: (actionId: string) => `${API_BASE}/api/reject/${actionId}`,
  action: (actionId: string) => `${API_BASE}/api/actions/${actionId}`,
  chat: () => `${API_BASE}/api/chat`,
};

export type Health = { status: string };

export async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(endpoints.health(), { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

// Discriminated union (audit #17) — at minimum every entry has a `kind` we
// can switch on. CLI tool calls produce {kind:'cli', tool, ...}, planner
// events produce {kind:'event', event, ...}. structlog emits free-form
// JSON today; we widen with [key:string]: unknown to stay forward-compatible
// without losing the strong-typing of the common fields.
export type CliLogEntry = {
  kind: "cli";
  timestamp?: string;
  level?: string;
  tool: string;
  params?: Record<string, unknown>;
  result_summary?: string;
  duration_ms?: number;
};

export type EventLogEntry = {
  kind: "event";
  timestamp?: string;
  level?: string;
  event: string;
  [key: string]: unknown;
};

export type LogEntry = CliLogEntry | EventLogEntry | {
  // Fallback for entries that match neither pattern — keep the renderer
  // resilient against new structlog event shapes.
  kind?: undefined;
  timestamp?: string;
  level?: string;
  event?: string;
  tool?: string;
  result_summary?: string;
  duration_ms?: number;
  [key: string]: unknown;
};

function normalizeLogEntry(raw: unknown): LogEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const e = raw as Record<string, unknown>;
  // Tag with `kind` so consumers can switch reliably.
  if (typeof e.tool === "string") {
    return { ...e, kind: "cli" } as CliLogEntry;
  }
  if (typeof e.event === "string") {
    return { ...e, kind: "event" } as EventLogEntry;
  }
  return e as LogEntry;
}

export async function getRecentLogs(limit = 20): Promise<LogEntry[]> {
  try {
    const res = await fetch(endpoints.logs(limit), { cache: "no-store" });
    if (!res.ok) return [];
    const body = (await res.json()) as unknown;
    if (!Array.isArray(body)) return [];
    return body
      .map(normalizeLogEntry)
      .filter((e): e is LogEntry => e !== null);
  } catch {
    return [];
  }
}
