"use client";

import { useEffect, useState } from "react";

import { getHealth } from "@/lib/api";

type Status = "checking" | "ok" | "down";

const STATUS_CONFIG: Record<
  Status,
  { label: string; dot: string; glyph: string }
> = {
  // glyph pairs color with a text/symbol so the indicator is readable
  // for colorblind users (audit #20).
  checking: { label: "CHECKING…", dot: "bg-ink-faint animate-pulse", glyph: "…" },
  ok:       { label: "HEALTHZ OK",  dot: "bg-ink",                  glyph: "✓" },
  down:     { label: "BACKEND DOWN", dot: "bg-terminal-red",         glyph: "✗" },
};

export default function BackendStatus({
  pollMs = 5000,
}: {
  pollMs?: number;
}) {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      const health = await getHealth();
      if (cancelled) return;
      setStatus(health?.status === "ok" ? "ok" : "down");
    };

    check();
    const interval = setInterval(check, pollMs);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [pollMs]);

  const cfg = STATUS_CONFIG[status];

  // Split the visible indicator from the screen-reader announcement so a
  // poll that returns the same status doesn't re-announce. The visible
  // element is `role="img"` with a static aria-label (one announcement
  // when first rendered); the SR-only live region uses `key={status}` to
  // force a remount on transitions, which is what triggers the actual
  // announcement. Without this split, NVDA and friends re-read the
  // status every 5s (audit #17).
  return (
    <>
      <span
        className="mono inline-flex items-center gap-1.5 text-[8px] tracking-wider text-ink-muted"
        role="img"
        aria-label={`Backend ${cfg.label}`}
      >
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${cfg.dot}`}
          aria-hidden="true"
        />
        <span aria-hidden="true">{cfg.glyph}</span>
        <span aria-hidden="true">{cfg.label}</span>
      </span>
      <span key={status} className="sr-only" role="status">
        Backend {cfg.label}
      </span>
    </>
  );
}
