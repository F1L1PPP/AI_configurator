"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import EthernetLogo from "@/components/mesh/EthernetLogo";

type IconName =
  | "dashboard"
  | "devices"
  | "ai"
  | "config"
  | "templates"
  | "logs"
  | "settings"
  | "preview"
  | "live";

const ICON_LABELS: Record<IconName, string> = {
  dashboard: "Dashboard",
  devices:   "Devices",
  ai:        "AI Chat",
  config:    "Configurations",
  templates: "Templates",
  logs:      "Logs",
  settings:  "Settings",
  preview:   "Preview",
  live:      "WebUI Live",
};

const Icon = ({ name }: { name: IconName }) => {
  // SVGs get role='img' + aria-label so screen readers announce them (audit #19).
  const common = {
    width: 14,
    height: 14,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    role: "img" as const,
    "aria-label": ICON_LABELS[name],
  };
  switch (name) {
    case "dashboard":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <rect x="3" y="3" width="7" height="9" />
          <rect x="14" y="3" width="7" height="5" />
          <rect x="14" y="12" width="7" height="9" />
          <rect x="3" y="16" width="7" height="5" />
        </svg>
      );
    case "devices":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <rect x="2" y="6" width="20" height="12" rx="1" />
          <line x1="6" y1="12" x2="18" y2="12" />
        </svg>
      );
    case "ai":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M8 10c1-2 7-2 8 0M8 14c1 2 7 2 8 0" />
        </svg>
      );
    case "config":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <path d="M4 6h16M4 12h16M4 18h16" />
          <circle cx="7" cy="6" r="1.5" fill="currentColor" />
          <circle cx="14" cy="12" r="1.5" fill="currentColor" />
          <circle cx="10" cy="18" r="1.5" fill="currentColor" />
        </svg>
      );
    case "preview":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <rect x="3" y="3" width="18" height="14" rx="1" />
          <path d="M3 12l5-3 4 2 4-3 5 2" />
          <line x1="8" y1="20" x2="16" y2="20" />
        </svg>
      );
    case "live":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <circle cx="12" cy="12" r="4" />
          <circle cx="12" cy="12" r="9" />
        </svg>
      );
    case "templates":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <rect x="3" y="3" width="18" height="18" rx="1" />
          <line x1="3" y1="9" x2="21" y2="9" />
          <line x1="9" y1="9" x2="9" y2="21" />
        </svg>
      );
    case "logs":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <path d="M5 4h14v16H5z" />
          <line x1="8" y1="9" x2="16" y2="9" />
          <line x1="8" y1="13" x2="16" y2="13" />
          <line x1="8" y1="17" x2="13" y2="17" />
        </svg>
      );
    case "settings":
      return (
        <svg viewBox="0 0 24 24" {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
        </svg>
      );
  }
};

type NavItem = { label: string; icon: IconName; href: string };

// Only pages that actually exist + work. The System section used to list
// Devices / Configurations / Templates / Logs / Settings — all dead links
// that confused users into thinking the app was broken. They come back
// as real pages get built (Day 8 ships Logs / Backups / Devices per
// PROJECT_PLAN.md §7 Day 8).
const mainNav: NavItem[] = [
  { label: "Dashboard", icon: "dashboard", href: "/" },
  { label: "AI Chat", icon: "ai", href: "/chat" },
  { label: "Quick Actions", icon: "config", href: "/actions" },
  { label: "Preview", icon: "preview", href: "/preview" },
  { label: "WebUI Live", icon: "live", href: "/webui-live" },
];

function NavSection({ label, items }: { label: string; items: NavItem[] }) {
  const pathname = usePathname();

  return (
    <div className="py-3">
      <div className="tech-label px-4 pb-1.5">{label}</div>
      {items.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/"
            : pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-2 border-l-2 px-4 py-1.5 text-[10px] tracking-wide transition-colors ${
              active
                ? "border-ink bg-page font-medium text-ink"
                : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            <span className={active ? "opacity-100" : "opacity-60"}>
              <Icon name={item.icon} />
            </span>
            <span>{item.label}</span>
          </Link>
        );
      })}
    </div>
  );
}

export default function Sidebar() {
  return (
    <aside className="flex w-[180px] min-w-[180px] flex-col border-r border-rule bg-sidebar">
      <div className="flex items-center gap-2 border-b border-rule px-4 py-4 text-ink">
        <EthernetLogo size={28} strokeWidth={1.1} />
        <div className="leading-tight">
          <div className="mono text-[10px] font-semibold tracking-wider">CISCO AI</div>
          <div className="mono text-[10px] font-semibold tracking-wider">CONFIG</div>
          <div className="text-[7px] tracking-wider text-ink-subtle">AI-POWERED</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto">
        <NavSection label="Main" items={mainNav} />
      </nav>

      <div className="border-t border-rule px-4 py-3">
        <div className="mono text-[8px] tracking-wider text-ink-ghost">
          <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-ink align-middle" />
          v0.0.1-bootstrap
        </div>
      </div>
    </aside>
  );
}
