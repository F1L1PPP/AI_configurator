"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import ApprovalButtons from "@/components/preview/ApprovalButtons";
import { endpoints } from "../../lib/api";
import {
  describeNetworkError,
  fromResponse,
  type FriendlyError,
} from "../../lib/errors";
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
  /** Friendly error payload from lib/errors.ts when the chat request
   *  failed. Renders as a titled banner with hint + collapsible detail
   *  instead of the previous raw `Error: HTTP 503` string. */
  friendlyError?: FriendlyError & { detail?: string };
  // Set on agent replies that proposed an action — renders inline
  // APPROVE / REJECT / EXECUTE NOW buttons under the bubble so the
  // user doesn't have to navigate to /preview.
  actionId?: string;
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
      <div
        className={`flex ${msg.actionId ? "max-w-[85%]" : "max-w-[70%]"} flex-col gap-1 ${
          isUser ? "items-end" : ""
        }`}
      >
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
        {msg.friendlyError && (
          <div className="border border-terminal-red bg-surface p-2">
            <p className="mono text-[9px] font-semibold tracking-wider text-terminal-red">
              ✗ {msg.friendlyError.title.toUpperCase()}
            </p>
            <p className="mono mt-1 text-[9px] leading-relaxed text-ink-muted">
              {msg.friendlyError.hint}
            </p>
            {msg.friendlyError.detail && (
              <details className="mono mt-1 text-[8px] text-ink-faint">
                <summary className="cursor-pointer tracking-wider">DETAIL</summary>
                <pre className="mt-1 whitespace-pre-wrap break-words">
                  {msg.friendlyError.detail}
                </pre>
              </details>
            )}
          </div>
        )}
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
        {msg.actionId && (
          <div className="w-full pt-1">
            <div className="mono mb-1 text-[8px] tracking-wider text-ink-faint">
              ACTION: {msg.actionId}
            </div>
            <ApprovalButtons actionId={msg.actionId} />
          </div>
        )}
        <span className="mono text-[8px] tracking-wider text-ink-line">{msg.time}</span>
      </div>
    </div>
  );
};

// Synthetic local-only event type. Inserted into the event stream the
// instant SEND is clicked so the operator gets immediate "yes, the
// agent is working on it" feedback instead of staring at an empty
// stream for the 1-5s it takes the planner to emit its first
// agent_thinking. Review §3 HIGH. Marked optional in event handling
// because the backend will never emit this type.
type LocalAgentEvent = AgentEvent | { type: "_pending"; ts: string; data: { text: string } };

function eventLabel(ev: LocalAgentEvent): string {
  switch (ev.type) {
    case "_pending":
      return `⋯ ${ev.data.text}`;
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
  const [events, setEvents] = useState<LocalAgentEvent[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState<unknown[]>([]);
  const [wsStatus, setWsStatus] = useState<WsStatus>("closed");
  const scrollRef = useRef<HTMLDivElement>(null);
  const eventScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handle = connectAgentWs(
      // Cap retained events at 200 so a long-running session doesn't bloat
      // React state. The UI only renders the last 30 anyway.
      (ev) => setEvents((prev) => [...prev.slice(-199), ev]),
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

    // Synthetic pending event — gives the operator instant feedback in
    // the live stream while the planner spins up. The backend never
    // emits this type; we strip it on the way out so the stream
    // doesn't accumulate one pending line per send. The unique ts
    // value lets us filter exactly this event later, ignoring any
    // other pending events that might be in-flight from a prior send.
    const pendingTs = `pending-${Date.now()}`;
    setEvents((prev) => [
      ...prev.slice(-199),
      {
        type: "_pending",
        ts: pendingTs,
        data: { text: "sending to agent…" },
      },
    ]);
    const removePending = () =>
      setEvents((prev) => prev.filter((ev) => !(ev.type === "_pending" && ev.ts === pendingTs)));

    try {
      const res = await fetch(endpoints.chat(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history }),
      });
      if (!res.ok) {
        const friendly = await fromResponse(res);
        setMessages((prev) => [
          ...prev,
          {
            id: `e-${Date.now()}`,
            role: "agent",
            text: friendly.title,
            time: nowTime(),
            error: true,
            friendlyError: friendly,
          },
        ]);
        return;
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
          // When the planner proposed a write, the response carries the
          // action_id. Surface it on the bubble so inline APPROVE /
          // EXECUTE NOW buttons render directly under the agent reply.
          // No /preview navigation needed.
          actionId: body.awaiting_approval ?? undefined,
        },
      ]);
      setHistory(body.history);
    } catch (err) {
      const friendly = describeNetworkError(err);
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}`,
          role: "agent",
          text: friendly.title,
          time: nowTime(),
          error: true,
          friendlyError: friendly,
        },
      ]);
    } finally {
      setSending(false);
      removePending();
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
            aria-label="Send message"
            className="mono border border-ink bg-ink px-3 py-1 text-[8px] tracking-wider text-surface disabled:opacity-50"
          >
            {sending ? "SENDING..." : "SEND"}
          </button>
        </div>
      </form>
    </div>
  );
}
