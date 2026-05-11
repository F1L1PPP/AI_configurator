"use client";

import { usePathname } from "next/navigation";

const TITLES: Record<string, { title: string; breadcrumb: string }> = {
  "/": { title: "Dashboard", breadcrumb: "/ home" },
  "/chat": { title: "AI Chat", breadcrumb: "/ chat" },
  "/preview": { title: "Configuration Preview", breadcrumb: "/ preview" },
  "/webui-live": { title: "WebUI Agent — Live", breadcrumb: "/ webui-live" },
  "/devices": { title: "Devices", breadcrumb: "/ devices" },
  "/config": { title: "Configurations", breadcrumb: "/ config" },
  "/templates": { title: "Templates", breadcrumb: "/ templates" },
  "/logs": { title: "Logs", breadcrumb: "/ logs" },
  "/settings": { title: "Settings", breadcrumb: "/ settings" },
};

export default function TopBar() {
  const pathname = usePathname();
  const meta = TITLES[pathname] ?? { title: "Cisco AI Config", breadcrumb: pathname };

  return (
    <header className="flex min-h-[46px] items-center justify-between border-b border-rule-soft px-5 py-3">
      <div className="flex items-center gap-3">
        <div className="text-[12px] font-semibold tracking-wide">{meta.title}</div>
        <span className="mono text-[9px] tracking-wider text-ink-faint">{meta.breadcrumb}</span>
      </div>

      <div className="flex items-center gap-3">
        <span className="mono inline-flex items-center gap-1.5 border border-ink px-2 py-0.5 text-[8px] tracking-wider">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-ink" />
          AGENT ACTIVE
        </span>
        <span className="mono text-[9px] tracking-wider text-ink-subtle">FILIP</span>
      </div>
    </header>
  );
}
