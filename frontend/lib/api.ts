export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Health = { status: string };

export async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/healthz`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export type LogEntry = {
  timestamp?: string;
  level?: string;
  event?: string;
  tool?: string;
  result_summary?: string;
  duration_ms?: number;
  [key: string]: unknown;
};

export async function getRecentLogs(limit = 20): Promise<LogEntry[]> {
  try {
    const res = await fetch(`${API_BASE}/api/logs/recent?limit=${limit}`, {
      cache: "no-store",
    });
    if (!res.ok) return [];
    return (await res.json()) as LogEntry[];
  } catch {
    return [];
  }
}
