// Numbered step list for the WebUI Agent Live screen.
// Day 4: pure presentational, props-driven. Day 5: page will subscribe to
// the WebSocket event stream and update steps in real time as the flow runs.

import type { PhaseStatus } from "./PhaseProgress";

export interface AgentAction {
  n: string;
  text: string;
  status: PhaseStatus;
}

const boxClasses = (status: PhaseStatus) => {
  if (status === "done") return "border-ink bg-ink text-surface";
  if (status === "current") return "border-ink animate-pulse";
  return "border-rule";
};

const textClasses = (status: PhaseStatus) => {
  if (status === "future") return "text-ink-line";
  if (status === "current") return "font-medium";
  return "";
};

function ActionRow({ a }: { a: AgentAction }) {
  return (
    <div className="flex items-start gap-2 border-b border-rule-ghost py-2 text-[10px] last:border-0">
      <span className="mono w-5 shrink-0 text-right text-[8px] text-ink-line">
        {a.n}
      </span>
      <span
        className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center border text-[8px] ${boxClasses(a.status)}`}
      >
        {a.status === "done" ? "✓" : ""}
      </span>
      <span className={`flex-1 leading-snug ${textClasses(a.status)}`}>
        {a.text}
      </span>
    </div>
  );
}

export default function ActionTimeline({ actions }: { actions: AgentAction[] }) {
  if (actions.length === 0) {
    return (
      <p className="mono text-[8px] tracking-wider text-ink-faint">
        NO ACTIONS YET — start a chat that triggers a webui_* tool
      </p>
    );
  }
  return (
    <div>
      {actions.map((a) => (
        <ActionRow key={a.n} a={a} />
      ))}
    </div>
  );
}
