import MeshSphere from "@/components/mesh/MeshSphere";
import RecentActions from "@/components/dashboard/RecentActions";
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

export default function DashboardPage() {
  return (
    <div className="relative flex flex-col gap-4">
      <div className="pointer-events-none absolute -right-10 -bottom-10 opacity-100">
        <MeshSphere size={220} opacity={0.08} />
      </div>
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
          <RecentActions limit={4} />
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
          &quot;Recent Activity&quot; panel polls{" "}
          <span className="mono">GET /api/logs/recent</span> every 3 s and shows the last 4
          entries from <span className="mono">logs/actions.log</span>.
        </p>
      </Panel>
    </div>
  );
}
