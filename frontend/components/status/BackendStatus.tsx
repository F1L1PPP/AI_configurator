"use client";

import { useEffect, useState } from "react";

import { getHealth } from "@/lib/api";

type Status = "checking" | "ok" | "down";

const STATUS_CONFIG: Record<Status, { label: string; dot: string }> = {
  checking: { label: "CHECKING…", dot: "bg-ink-faint" },
  ok: { label: "HEALTHZ OK", dot: "bg-ink" },
  down: { label: "BACKEND DOWN", dot: "bg-terminal-red" },
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
    <span className="mono inline-flex items-center gap-1.5 text-[8px] tracking-wider text-ink-muted">
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  );
}
