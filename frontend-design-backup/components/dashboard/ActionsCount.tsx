"use client";

import { useEffect, useState } from "react";

import { getRecentLogs, type LogEntry } from "@/lib/api";

/**
 * Live count of tool-call entries from /api/logs/recent that landed
 * today (router-local clock). Replaces the previous mocked
 * `Actions: 12` stat card on the Dashboard.
 *
 * Reads up to the last 200 log lines (the backend caps at this), filters
 * to entries where `kind === "cli"` (a successful tool call) and the
 * timestamp falls on today's date, and shows the count.
 *
 * "Today" is the client's local date. Logs are UTC ISO timestamps; we
 * compare against `new Date(timestamp).toDateString() === new Date().toDateString()`
 * which works correctly across timezones.
 */
export default function ActionsCount() {
  const [count, setCount] = useState<number | null>(null);
  const [errored, setErrored] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const entries = await getRecentLogs(200);
        if (cancelled) return;
        const today = new Date().toDateString();
        const todayCount = entries.filter((e: LogEntry) => {
          if (e.kind !== "cli") return false;
          if (!e.timestamp) return false;
          try {
            return new Date(e.timestamp).toDateString() === today;
          } catch {
            return false;
          }
        }).length;
        setCount(todayCount);
        setErrored(false);
      } catch {
        if (!cancelled) setErrored(true);
      }
    }

    tick();
    const id = setInterval(tick, 5_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (errored) {
    return (
      <div className="mono text-[10px] text-ink-line">
        — (backend unreachable)
      </div>
    );
  }
  if (count === null) {
    return <div className="mono text-[10px] text-ink-line">…</div>;
  }
  return (
    <div className="flex flex-col gap-0.5">
      <div className="text-[26px] font-light leading-none tracking-tight">
        {count}
      </div>
      <div className="mono text-[8px] tracking-wider text-ink-faint">
        TOOL CALLS · UPDATED EVERY 5 S
      </div>
    </div>
  );
}
