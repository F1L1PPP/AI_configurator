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

  return (
    <span
      className="mono inline-flex items-center gap-1.5 text-[8px] tracking-wider text-ink-muted"
      role="status"
      aria-live="polite"
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${cfg.dot}`}
        aria-hidden="true"
      />
      <span aria-hidden="true">{cfg.glyph}</span>
      <span>{cfg.label}</span>
    </span>
  );
}
