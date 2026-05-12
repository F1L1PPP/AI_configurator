// Phase strip for the WebUI Agent Live screen.
// Day 4: pure presentational, props-driven. Day 5: page will pass real
// phase state derived from WebSocket events.

export type PhaseStatus = "done" | "current" | "future";

export interface Phase {
  n: string;
  label: string;
  status: PhaseStatus;
}

const ringClasses = (status: PhaseStatus) => {
  if (status === "done") return "border-ink bg-ink text-surface";
  if (status === "current") return "border-ink bg-surface text-ink";
  return "border-rule text-ink-line";
};

const labelClasses = (status: PhaseStatus) =>
  status === "future" ? "text-ink-line" : "text-ink";

export default function PhaseProgress({ phases }: { phases: Phase[] }) {
  return (
    <div className="flex items-center gap-3">
      {phases.map((p, idx) => (
        <div key={p.n} className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div
              className={`mono flex h-6 w-6 items-center justify-center rounded-full border text-[9px] ${ringClasses(p.status)}`}
            >
              {p.n}
            </div>
            <span className={`text-[10px] tracking-wide ${labelClasses(p.status)}`}>
              {p.label}
            </span>
          </div>
          {idx < phases.length - 1 ? (
            <span
              className={`h-px w-8 ${p.status === "done" ? "bg-ink" : "bg-rule"}`}
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}
