"use client";

import { useEffect, useState } from "react";

import { getRecentLogs, type LogEntry } from "@/lib/api";

function label(entry: LogEntry): string {
  // Discriminated union switch — narrowed types per branch.
  if (entry.kind === "cli") return `${entry.tool} (CLI)`;
  if (entry.kind === "event") return entry.event;
  // Fallback for un-tagged entries (format drift or unknown shape)
  const fallback = entry as { tool?: string; event?: string };
  if (typeof fallback.tool === "string") return `${fallback.tool} (CLI)`;
  if (typeof fallback.event === "string") return fallback.event;
  return "log entry";
}

function shortTime(entry: LogEntry): string {
  if (!entry.timestamp) return "";
  try {
    return new Date(entry.timestamp as string).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function ActionRow({ entry, id }: { entry: LogEntry; id: number }) {
  const ok = entry.level !== "error" && entry.level !== "warning";
  return (
    <div className="flex items-center gap-2 border-b border-rule-ghost py-1.5 text-[10px] last:border-0">
      <span className="mono w-5 shrink-0 text-right text-[8px] text-ink-line">
        {String(id).padStart(2, "0")}
      </span>
      <span
        className={`flex h-5 w-5 shrink-0 items-center justify-center border ${
          ok ? "border-ink bg-ink text-surface" : "border-terminal-red"
        } text-[9px]`}
        aria-hidden
      >
        {ok ? "✓" : "!"}
      </span>
      <span className="flex-1 leading-snug">{label(entry)}</span>
      {typeof entry.duration_ms === "number" && (
        <span className="mono shrink-0 text-[8px] text-ink-faint">
          {entry.duration_ms}ms
        </span>
      )}
      <span className="mono shrink-0 text-[8px] text-ink-line">
        {shortTime(entry)}
      </span>
    </div>
  );
}

function EmptyState() {
  return (
    <p className="text-[10px] text-ink-faint">
      No actions yet — start the backend and trigger a{" "}
      <span className="mono">show_*</span> command.
    </p>
  );
}

export default function RecentActions({ limit = 4 }: { limit?: number }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      const data = await getRecentLogs(limit);
      if (!cancelled) setEntries(data);
    };

    poll();
    const interval = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [limit]);

  if (entries.length === 0) return <EmptyState />;

  return (
    <div>
      {entries.slice(0, limit).map((entry, i) => (
        // Stable key (audit #13): timestamp+tool/event uniquely identifies
        // the log line; fall back to index only as a last resort. With a
        // stable key React reuses the right DOM node when the poll
        // reorders/prepends rows, avoiding the animation/focus glitches
        // that index-as-key caused.
        <ActionRow
          key={keyFor(entry, i)}
          entry={entry}
          id={i + 1}
        />
      ))}
    </div>
  );
}

function keyFor(entry: LogEntry, fallbackIndex: number): string {
  const ts = entry.timestamp ?? "";
  const what = (entry as { tool?: string; event?: string }).tool
    ?? (entry as { tool?: string; event?: string }).event
    ?? "log";
  if (ts) return `${ts}|${what}`;
  return `idx-${fallbackIndex}`;
}
