import LiveEventStream from "@/components/LiveEventStream";
import MeshSphere from "@/components/mesh/MeshSphere";
import ActionTimeline, {
  type AgentAction,
} from "@/components/webui-agent/ActionTimeline";
import PhaseProgress, {
  type Phase,
} from "@/components/webui-agent/PhaseProgress";

// Phase progress + screenshot pane stay mocked for now (no backend signal
// for phases or screenshot stream yet). The "Live Activity" panel below
// consumes real planner events via /ws/agent.

const phaseSteps: Phase[] = [
  { n: "01", label: "Prompt", status: "done" },
  { n: "02", label: "Plan", status: "done" },
  { n: "03", label: "Approval", status: "done" },
  { n: "04", label: "Execution", status: "current" },
  { n: "05", label: "Verify", status: "future" },
];

const agentActions: AgentAction[] = [
  { n: "01", text: "Open WebUI (Playwright launch)", status: "done" },
  { n: "02", text: "Login as admin", status: "done" },
  { n: "03", text: "Navigate Configuration → VLAN", status: "done" },
  { n: "04", text: "Click 'Add New VLAN'", status: "done" },
  { n: "05", text: "Fill VLAN ID = 30, Name = OFFICE", status: "current" },
  { n: "06", text: "Select interface Gi0/0/1, mode access", status: "future" },
  { n: "07", text: "Click 'Save'", status: "future" },
  { n: "08", text: "Screenshot post-state, verify via CLI", status: "future" },
];

export default function WebUILivePage() {
  return (
    <div className="relative flex flex-col gap-4">
      <div className="pointer-events-none absolute -right-12 -bottom-12 z-0">
        <MeshSphere size={260} opacity={0.06} />
      </div>

      <section className="relative z-10 border border-rule bg-surface px-4 py-3">
        <div className="mb-3 flex items-center justify-between">
          <span className="tech-label">Phase progress</span>
          <span className="mono text-[8px] tracking-wider text-ink-faint">
            ACT_2026-05-11_8AF3C2 · WEBUI_ADD_ACCESS_VLAN
          </span>
        </div>
        <PhaseProgress phases={phaseSteps} />
      </section>

      <div className="relative z-10 grid grid-cols-[1fr_280px] gap-4">
        <section className="border border-rule bg-surface">
          <div className="flex items-center justify-between border-b border-rule-soft bg-sidebar px-3 py-1.5">
            <div className="mono flex items-center gap-2 text-[9px] tracking-wider text-ink-muted">
              <span className="h-2 w-2 rounded-full bg-terminal-red" />
              <span className="h-2 w-2 rounded-full bg-terminal-yellow" />
              <span className="h-2 w-2 rounded-full bg-terminal-green" />
              <span className="ml-2">https://192.168.10.1/webui/#/configuration/vlan</span>
            </div>
            <span className="mono text-[8px] tracking-wider text-ink-faint">PLAYWRIGHT · HEADED</span>
          </div>

          <div className="flex h-[420px] flex-col items-center justify-center gap-3 bg-page p-6">
            <div className="mono text-[9px] tracking-wider text-ink-faint">
              SCREENSHOT 05_VLAN_FORM.PNG
            </div>
            <div className="w-full max-w-[540px] border border-rule bg-surface p-5">
              <div className="mb-4 border-b border-rule-soft pb-2">
                <div className="text-[11px] font-semibold tracking-wide">
                  Add VLAN — Cisco IOS XE WebUI
                </div>
                <div className="mono text-[8px] tracking-wider text-ink-faint">
                  CONFIGURATION → VLAN → ADD NEW
                </div>
              </div>

              <div className="space-y-3">
                <div>
                  <div className="mono text-[8px] tracking-wider text-ink-faint">
                    VLAN ID
                  </div>
                  <div className="mono mt-0.5 border border-ink bg-page px-2 py-1 text-[10px]">
                    30<span className="ml-0.5 animate-pulse">|</span>
                  </div>
                </div>
                <div>
                  <div className="mono text-[8px] tracking-wider text-ink-faint">
                    VLAN Name
                  </div>
                  <div className="mono mt-0.5 border border-rule bg-page px-2 py-1 text-[10px] text-ink-muted">
                    OFFICE
                  </div>
                </div>
                <div>
                  <div className="mono text-[8px] tracking-wider text-ink-faint">
                    Interface
                  </div>
                  <div className="mono mt-0.5 border border-rule bg-page px-2 py-1 text-[10px] text-ink-line">
                    — pending —
                  </div>
                </div>
                <div className="pt-2">
                  <button
                    disabled
                    className="mono w-full border border-ink bg-ink px-3 py-1.5 text-[8px] tracking-wider text-surface opacity-50"
                  >
                    SAVE
                  </button>
                </div>
              </div>
            </div>
            <div className="mono mt-2 text-[8px] tracking-wider text-ink-line">
              ARTIFACTS/SCREENSHOTS/&lt;SESSION&gt;/05_VLAN_FORM.PNG · DAY 5 WIRES REAL STREAM
            </div>
          </div>
        </section>

        <aside className="flex flex-col gap-3">
          <div className="border border-rule bg-surface p-3.5">
            <div className="tech-label mb-2">AI Next Actions</div>
            <ActionTimeline actions={agentActions} />
          </div>
          <LiveEventStream emptyText="WAITING FOR WEBUI AGENT..." />
        </aside>
      </div>

      <section className="relative z-10 border border-rule bg-surface">
        <div className="border-b border-rule-soft bg-sidebar px-3.5 py-2">
          <span className="tech-label">Verification Result</span>
        </div>
        <div className="flex items-center gap-3 p-3.5">
          <span className="mono inline-flex items-center gap-1.5 border border-rule px-2 py-0.5 text-[8px] tracking-wider text-ink-line">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-rule" />
            PENDING
          </span>
          <span className="text-[10px] leading-relaxed text-ink-muted">
            Verification runs after step 08 — CLI <span className="mono">show vlan brief</span>{" "}
            must list VLAN 30 named OFFICE on Gi0/0/1, and the WebUI list page must show the
            new row.
          </span>
        </div>
      </section>
    </div>
  );
}
