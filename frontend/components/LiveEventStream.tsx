"use client";

import { useEffect, useRef, useState } from "react";

import { AgentEvent, connectAgentWs } from "../lib/ws";

type WsStatus = "open" | "closed" | "error";

function eventLabel(ev: AgentEvent): string {
  switch (ev.type) {
    case "agent_thinking":
      return `thinking · iter ${ev.data.iteration}`;
    case "tool_call":
      return `→ ${ev.data.name}`;
    case "tool_result":
      return `✓ ${ev.data.name}`;
    case "awaiting_approval":
      return `⏸ awaiting · ${ev.data.action_id}`;
    case "applied":
      return `✓ applied · ${ev.data.tool} (${ev.data.duration_ms ?? "?"}ms)`;
    case "verified":
      return `✓ verified`;
    case "error":
      return `✗ ${ev.data.message}`;
  }
}

export default function LiveEventStream({
  filter,
  emptyText = "WAITING FOR AGENT ACTIVITY...",
}: {
  filter?: (ev: AgentEvent) => boolean;
  emptyText?: string;
}) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<WsStatus>("closed");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handle = connectAgentWs(
      (ev) => {
        if (!filter || filter(ev)) {
          setEvents((prev) => [...prev, ev]);
        }
      },
      (s) => setStatus(s),
    );
    return () => handle.close();
  }, [filter]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [events]);

  const dotClass =
    status === "open"
      ? "bg-green-600"
      : status === "error"
        ? "bg-red-600"
        : "bg-ink-line";

  return (
    <div className="border border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule-soft bg-sidebar px-3 py-1.5">
        <span className="tech-label">Live Activity</span>
        <span className="mono inline-flex items-center gap-1.5 text-[8px] tracking-wider text-ink-faint">
          <span className={`inline-block h-1.5 w-1.5 rounded-full ${dotClass}`} />
          WS {status.toUpperCase()}
        </span>
      </div>
      <div ref={scrollRef} className="max-h-48 overflow-y-auto px-3 py-2">
        {events.length === 0 ? (
          <div className="mono py-2 text-[9px] tracking-wider text-ink-faint">
            {emptyText}
          </div>
        ) : (
          events.slice(-50).map((ev, i) => (
            <div
              key={`${ev.ts}-${i}`}
              title={ev.ts}
              className="mono py-0.5 text-[10px] text-ink-muted"
            >
              {eventLabel(ev)}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
