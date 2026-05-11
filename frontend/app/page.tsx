import BackendStatus from "@/components/status/BackendStatus";

type StatProps = { label: string; value: string | number; sub?: string };
const StatCard = ({ label, value, sub }: StatProps) => (
  <div className="border border-rule bg-surface p-4">
    <div className="tech-label mb-1.5">{label}</div>
    <div className="text-[26px] font-light leading-none tracking-tight">{value}</div>
    {sub ? <div className="mt-1 text-[8px] text-ink-faint">{sub}</div> : null}
  </div>
);

type PanelProps = { title: string; right?: React.ReactNode; children: React.ReactNode };
const Panel = ({ title, right, children }: PanelProps) => (
  <section className="border border-rule bg-surface">
    <header className="flex items-center justify-between border-b border-rule-soft bg-sidebar px-3.5 py-2">
      <span className="tech-label">{title}</span>
      {right}
    </header>
    <div className="p-3.5">{children}</div>
  </section>
);

const mockActions: { id: string; text: string; time: string; status: "ok" | "pending" }[] = [
  { id: "01", text: "show running-config (CLI)", time: "10:34", status: "ok" },
  { id: "02", text: "set hostname LAB-R1 (CLI, approved)", time: "10:31", status: "ok" },
  { id: "03", text: "show vlan brief (CLI)", time: "10:28", status: "ok" },
  { id: "04", text: "Bootstrap healthz check", time: "10:25", status: "ok" },
];

const ActionRow = ({ row }: { row: (typeof mockActions)[number] }) => (
  <div className="flex items-center gap-2 border-b border-rule-ghost py-1.5 text-[10px] last:border-0">
    <span className="mono w-5 shrink-0 text-right text-[8px] text-ink-line">{row.id}</span>
    <span
      className={`flex h-5 w-5 shrink-0 items-center justify-center border ${
        row.status === "ok" ? "border-ink bg-ink text-surface" : "border-rule"
      } text-[9px]`}
      aria-hidden
    >
      {row.status === "ok" ? "✓" : "·"}
    </span>
    <span className="flex-1 leading-snug">{row.text}</span>
    <span className="mono shrink-0 text-[8px] text-ink-line">{row.time}</span>
  </div>
);

export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Devices" value={1} sub="C1111 connected (mock)" />
        <StatCard label="Sessions today" value={3} sub="0 failed" />
        <StatCard label="Actions" value={12} sub="All HITL-approved" />
      </div>

      <div className="grid grid-cols-[1fr_280px] gap-4">
        <Panel
          title="Recent Activity"
          right={<span className="mono text-[8px] tracking-wider text-ink-faint">LAST 24H</span>}
        >
          <div>
            {mockActions.map((row) => (
              <ActionRow key={row.id} row={row} />
            ))}
          </div>
        </Panel>

        <Panel title="Quick Actions">
          <div className="flex flex-col gap-2">
            <button className="mono border border-ink px-3 py-1.5 text-left text-[8px] tracking-wider hover:bg-ink hover:text-surface">
              + NEW AI SESSION
            </button>
            <button className="mono border border-rule px-3 py-1.5 text-left text-[8px] tracking-wider text-ink-muted hover:border-ink hover:text-ink">
              CONNECT DEVICE
            </button>
            <button className="mono border border-rule px-3 py-1.5 text-left text-[8px] tracking-wider text-ink-muted hover:border-ink hover:text-ink">
              VIEW LOGS
            </button>
            <button className="mono border border-rule px-3 py-1.5 text-left text-[8px] tracking-wider text-ink-muted hover:border-ink hover:text-ink">
              BACKUP CONFIG
            </button>
          </div>
        </Panel>
      </div>

      <Panel title="Backend status" right={<BackendStatus pollMs={5000} />}>
        <p className="text-[10px] leading-relaxed text-ink-muted">
          The status dot above polls <span className="mono">GET /healthz</span> on the FastAPI
          backend every 5 seconds. Start it with{" "}
          <span className="mono">uvicorn backend.main:app --reload</span>. The
          &quot;Recent Activity&quot; panel still uses mocked rows; Day 2 wires it to the
          structlog JSONL stream at <span className="mono">logs/actions.log</span>.
        </p>
      </Panel>
    </div>
  );
}
