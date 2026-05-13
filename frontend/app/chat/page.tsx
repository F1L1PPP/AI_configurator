"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { endpoints } from "../../lib/api";
import {
  AgentEvent,
  AgentSource,
  connectAgentWs,
  extractSources,
} from "../../lib/ws";

type Msg = {
  id: string;
  role: "user" | "agent";
  text: string;
  time: string;
  sources?: AgentSource[];
  error?: boolean;
};

type WsStatus = "open" | "closed" | "error";

const TIME_FMT: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit" };

function nowTime(): string {
  return new Date().toLocaleTimeString(undefined, TIME_FMT);
}

const Bubble = ({ msg }: { msg: Msg }) => {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 px-1 py-2 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`mono flex h-6 w-6 shrink-0 items-center justify-center border text-[9px] tracking-wider ${
          isUser ? "border-ink bg-ink text-surface" : "border-ink text-ink"
        }`}
      >
        {isUser ? "U" : "AI"}
      </div>
      <div className={`flex max-w-[70%] flex-col gap-1 ${isUser ? "items-end" : ""}`}>
        <div
          className={`whitespace-pre-wrap border px-3 py-2 text-[11px] leading-relaxed ${
            isUser
              ? "border-ink bg-ink text-surface"
              : msg.error
                ? "border-rule bg-surface text-ink-muted"
                : "border-rule bg-surface text-ink"
          }`}
        >
          {msg.text}
        </div>
        {msg.sources && msg.sources.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-0.5">
            {msg.sources.map((s, i) => (
              <span
                key={`${msg.id}-${i}`}
                title={`${s.source} — ${s.section} (score ${s.score.toFixed(3)})`}
                className="mono inline-flex items-center gap-1.5 border border-rule px-2 py-0.5 text-[8px] tracking-wider text-ink-line"
              >
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-ink" />
                {s.source.replace(/\.pdf$/i, "")} · {s.section.slice(0, 40)}
              </span>
            ))}
          </div>
        )}
        <span className="mono text-[8px] tracking-wider text-ink-line">{msg.time}</span>
      </div>
    </div>
  );
};

function eventLabel(ev: AgentEvent): string {
  switch (ev.type) {
    case "agent_thinking":
      return `thinking · iter ${ev.data.iteration}`;
    case "tool_call":
      return `→ ${ev.data.name}(${truncateParams(ev.data.input)})`;
    case "tool_result":
      return `✓ ${ev.data.name}`;
    case "awaiting_approval":
      return `⏸ awaiting_approval · ${ev.data.action_id}`;
    case "applied":
      return `✓ applied · ${ev.data.tool} (${ev.data.duration_ms ?? "?"}ms)`;
    case "verified":
      return `✓ verified · ${ev.data.tool ?? ev.data.action_id ?? ""}`;
    case "error":
      return `✗ error · ${ev.data.message}`;
  }
}

function truncateParams(input: Record<string, unknown>): string {
  const s = JSON.stringify(input);
  return s.length > 60 ? s.slice(0, 57) + "..." : s;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState<unknown[]>([]);
  const [wsStatus, setWsStatus] = useState<WsStatus>("closed");
  const scrollRef = useRef<HTMLDivElement>(null);
  const eventScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handle = connectAgentWs(
      (ev) => setEvents((prev) => [...prev, ev]),
      (status) => setWsStatus(status),
    );
    return () => handle.close();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    eventScrollRef.current?.scrollTo({ top: eventScrollRef.current.scrollHeight });
  }, [events]);

  async function sendMessage(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: Msg = {
      id: `u-${Date.now()}`,
      role: "user",
      text,
      time: nowTime(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);

    try {
      const res = await fetch(endpoints.chat(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
      }
      const body = (await res.json()) as {
        final_text: string;
        events: AgentEvent[];
        history: unknown[];
        stop_reason: string;
        awaiting_approval: string | null;
      };
      const sources = extractSources(body.events);
      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "agent",
          text: body.final_text || "(empty reply)",
          time: nowTime(),
          sources: sources.length > 0 ? sources : undefined,
        },
      ]);
      setHistory(body.history);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}`,
          role: "agent",
          text: `Error: ${err instanceof Error ? err.message : String(err)}`,
          time: nowTime(),
          error: true,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  const wsDotClass =
    wsStatus === "open"
      ? "bg-green-600"
      : wsStatus === "error"
        ? "bg-red-600"
        : "bg-ink-line";

  return (
    <div className="flex h-[calc(100vh-90px)] flex-col gap-3">
      <section
        ref={scrollRef}
        className="flex-1 overflow-y-auto border border-rule bg-surface p-4"
      >
        <div className="mono mb-3 flex items-center justify-between border-b border-rule-soft pb-2 text-[8px] tracking-wider text-ink-faint">
          <span>
            SESSION · {messages.length} MESSAGES · MODEL CLAUDE-HAIKU-4-5
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${wsDotClass}`} />
            WS {wsStatus.toUpperCase()}
          </span>
        </div>
        {messages.length === 0 && (
          <div className="mono py-8 text-center text-[9px] tracking-wider text-ink-faint">
            START A CONVERSATION — TRY "AKO ZMENIM HOSTNAME NA C1111?"
          </div>
        )}
        {messages.map((m) => (
          <Bubble key={m.id} msg={m} />
        ))}
      </section>

      {events.length > 0 && (
        <section
          ref={eventScrollRef}
          className="max-h-32 overflow-y-auto border border-rule bg-surface px-3 py-2"
        >
          <div className="mono mb-1 text-[8px] tracking-wider text-ink-faint">
            LIVE EVENT STREAM · /WS/AGENT
          </div>
          {events.slice(-30).map((ev, i) => (
            <div
              key={`${ev.ts}-${i}`}
              className="mono py-0.5 text-[10px] text-ink-muted"
              title={ev.ts}
            >
              {eventLabel(ev)}
            </div>
          ))}
        </section>
      )}

      <form onSubmit={sendMessage} className="border border-rule bg-surface">
        <div className="flex items-center gap-2 px-3 py-2">
          <span className="mono text-[8px] tracking-wider text-ink-line">$</span>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask the agent — e.g. ako zmenim hostname na LAB-R5?"
            disabled={sending}
            className="mono flex-1 bg-transparent text-[11px] text-ink placeholder:text-ink-line focus:outline-none"
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="mono border border-ink bg-ink px-3 py-1 text-[8px] tracking-wider text-surface disabled:opacity-50"
          >
            {sending ? "SENDING..." : "SEND"}
          </button>
        </div>
      </form>
    </div>
  );
}
