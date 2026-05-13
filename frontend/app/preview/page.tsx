import LiveEventStream from "@/components/LiveEventStream";
import ApprovalButtons from "@/components/preview/ApprovalButtons";

const plannedActions = [
  {
    n: "01",
    tool: "webui_open_session",
    desc: "Launch Playwright Chromium against https://192.168.1.1",
  },
  {
    n: "02",
    tool: "webui_login",
    desc: "Authenticate as 'admin' (credentials from .env)",
  },
  {
    n: "03",
    tool: "webui_navigate",
    desc: "Configuration → Administration → Device Properties",
  },
  {
    n: "04",
    tool: "webui_set_hostname",
    desc: "Set hostname field to LAB-R1",
  },
  {
    n: "05",
    tool: "webui_click",
    desc: "Submit form, wait for confirmation banner",
  },
  {
    n: "06",
    tool: "cli_show_running_config",
    desc: "Verify via SSH: 'show running-config | i hostname' contains LAB-R1",
  },
];

const KeyVal = ({ k, v }: { k: string; v: string }) => (
  <div className="flex justify-between gap-3 py-1">
    <span className="mono text-[8px] tracking-wider text-ink-faint">{k}</span>
    <span className="mono text-[9px] text-ink">{v}</span>
  </div>
);

export default function PreviewPage({
  searchParams,
}: {
  searchParams?: { action_id?: string };
}) {
  const actionId = searchParams?.action_id;

  return (
    <div className="grid grid-cols-[1fr_280px] gap-4">
      <section className="flex flex-col gap-4">
        <header className="border border-rule bg-surface">
          <div className="border-b border-rule-soft bg-sidebar px-3.5 py-2">
            <span className="tech-label">Planned Actions</span>
          </div>
          <div className="p-1">
            {plannedActions.map((a) => (
              <div
                key={a.n}
                className="flex items-start gap-3 border-b border-rule-ghost px-3 py-2 last:border-0"
              >
                <span className="mono w-7 shrink-0 text-right text-[8px] text-ink-line">
                  {a.n}
                </span>
                <div className="flex h-5 w-5 shrink-0 items-center justify-center border border-rule text-[9px] text-ink-faint">
                  ·
                </div>
                <div className="flex-1">
                  <div className="mono text-[10px] tracking-wide">{a.tool}</div>
                  <div className="mt-0.5 text-[10px] leading-snug text-ink-muted">
                    {a.desc}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </header>

        <section className="border border-rule bg-surface">
          <div className="border-b border-rule-soft bg-sidebar px-3.5 py-2">
            <span className="tech-label">Change Summary</span>
          </div>
          <pre className="mono overflow-x-auto bg-terminal-bg p-3.5 text-[10px] leading-relaxed text-terminal-fg">
            {`- hostname Router-12345\n+ hostname LAB-R1`}
          </pre>
        </section>

        <ApprovalButtons actionId={actionId} />
        <LiveEventStream emptyText="WAITING FOR PLANNER EVENTS..." />
      </section>

      <aside className="flex flex-col gap-4">
        <section className="border border-rule bg-surface">
          <div className="border-b border-rule-soft bg-sidebar px-3.5 py-2">
            <span className="tech-label">Risk Assessment</span>
          </div>
          <div className="p-3.5">
            <div className="mb-2 text-[10px] uppercase tracking-wide text-ink-muted">
              Medium
            </div>
            <div className="mb-3 flex gap-1">
              <span className="h-2 flex-1 bg-ink" />
              <span className="h-2 flex-1 bg-ink" />
              <span className="h-2 flex-1 bg-rule" />
            </div>
            <ul className="space-y-1 text-[10px] leading-snug text-ink-muted">
              <li>· Hostname is system-wide</li>
              <li>· Pre-snapshot will be taken</li>
              <li>· Rollback available from backup</li>
              <li>· Affects SNMP / syslog identity</li>
            </ul>
          </div>
        </section>

        <section className="border border-rule bg-surface">
          <div className="border-b border-rule-soft bg-sidebar px-3.5 py-2">
            <span className="tech-label">Action Context</span>
          </div>
          <div className="p-3.5">
            <KeyVal k="ACTION_ID" v={actionId ?? "—"} />
            <KeyVal k="REQUESTED_BY" v="filip" />
            <KeyVal k="TARGET" v="192.168.10.1" />
            <KeyVal k="DEVICE" v="C1111 (LAB)" />
            <KeyVal k="TOOL" v="set_hostname" />
            <KeyVal k="WRITES" v="1" />
            <KeyVal k="READS" v="1 (verify)" />
          </div>
        </section>

        <section className="border border-rule bg-surface">
          <div className="border-b border-rule-soft bg-sidebar px-3.5 py-2">
            <span className="tech-label">Pre-snapshot</span>
          </div>
          <div className="space-y-1 p-3.5 text-[10px] text-ink-muted">
            <div>· running-config</div>
            <div>· show version</div>
            <div>· show ip int brief</div>
            <div className="mono mt-2 text-[8px] tracking-wider text-ink-line">
              artifacts/device-snapshots/{actionId ?? "act_…"}
            </div>
          </div>
        </section>
      </aside>
    </div>
  );
}
