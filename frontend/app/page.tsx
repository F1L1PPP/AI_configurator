import ScenarioCard from "@/components/actions/ScenarioCard";
import RecentActions from "@/components/dashboard/RecentActions";
import ActionsCount from "@/components/dashboard/ActionsCount";
import BackendStatus from "@/components/status/BackendStatus";

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
    <div className="flex flex-col gap-4">
      {/* The decorative MeshSphere used to live here (-right-10 -bottom-10
          absolute) but it overlapped the Backend Status / Actions Today
          panels at the new grid layout and looked weirdly placed.
          Removed entirely — Dashboard is functional, not decorative. */}

      {/* Quick Actions is now the primary CTA — full width, 2-column grid,
          larger cards. Filip's feedback: "make that quick actions more
          bigger". This replaces the previous narrow right-column panel. */}
      <Panel
        title="Quick Actions"
        right={
          <span className="mono text-[8px] tracking-wider text-ink-faint">
            CLICK A CARD · NO TYPING REQUIRED
          </span>
        }
      >
        <div className="grid grid-cols-2 gap-2.5">
          <ScenarioCard
            title="Change hostname"
            description="Rename the router. Pick CLI (fast) or WebUI (screenshots)."
            href="/actions/change-hostname"
            status="shipped"
            badge="CLI · WebUI"
          />
          <ScenarioCard
            title="Add access VLAN"
            description="Create a VLAN in the database. Pick CLI (fast) or WebUI (screenshots)."
            href="/actions/add-vlan"
            status="shipped"
            badge="CLI · WebUI"
          />
          <ScenarioCard
            title="Set interface IP"
            description="Assign an IPv4 address + mask to a GigabitEthernet interface."
            href="/actions/set-interface-ip"
            status="shipped"
            badge="CLI"
          />
          <ScenarioCard
            title="Ask a question"
            description="Open the chat — RAG-grounded answers with Cisco doc citations."
            href="/chat"
            status="shipped"
            badge="Chat · RAG"
          />
        </div>
      </Panel>

      {/* Real-data panels below — replaces the previous trio of mocked
          StatCards (Devices=1 / Sessions=3 / Actions=12 hardcoded).
          Recent Activity polls /api/logs/recent. ActionsCount derives a
          live count from the same log endpoint. */}
      <div className="grid grid-cols-[1fr_280px] gap-4">
        <Panel
          title="Recent Activity"
          right={
            <span className="mono text-[8px] tracking-wider text-ink-faint">
              POLLED FROM /api/logs/recent · LAST 4 ENTRIES
            </span>
          }
        >
          <RecentActions limit={4} />
        </Panel>

        <div className="flex flex-col gap-4">
          <Panel
            title="Backend Status"
            right={<BackendStatus pollMs={5000} />}
          >
            <p className="text-[10px] leading-relaxed text-ink-muted">
              Dot polls <span className="mono">GET /healthz</span> every
              5 s. Green = FastAPI backend is up.
            </p>
          </Panel>

          <Panel title="Actions Today">
            <ActionsCount />
          </Panel>
        </div>
      </div>
    </div>
  );
}
