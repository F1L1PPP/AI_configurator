"use client";

import Link from "next/link";

export type ScenarioStatus = "shipped" | "planned";

export interface ScenarioCardProps {
  title: string;
  description: string;
  href: string;
  status: ScenarioStatus;
  /** Short label shown on the right (e.g. "CLI", "WebUI"). */
  badge?: string;
}

/**
 * One launcher card for a §2 scenario.
 *
 * Used twice:
 *  - Dashboard "Quick Actions" panel — 6 cards in a tight column
 *  - /actions index page — same 6 cards but in a roomier grid
 *
 * `status="planned"` renders the card visually muted and disables the link
 * so users can see what's coming without misclicking into a dead route.
 */
export default function ScenarioCard({
  title,
  description,
  href,
  status,
  badge,
}: ScenarioCardProps) {
  const isShipped = status === "shipped";

  const card = (
    <div
      className={`flex h-full flex-col gap-1.5 border px-3 py-2 ${
        isShipped
          ? "border-rule bg-surface hover:border-ink hover:bg-page"
          : "border-rule-soft bg-surface opacity-50"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="mono text-[10px] tracking-wide text-ink">{title}</span>
        {badge && (
          <span className="mono inline-flex items-center gap-1.5 border border-rule px-1.5 py-0.5 text-[8px] tracking-wider text-ink-line">
            {badge}
          </span>
        )}
      </div>
      <p className="text-[10px] leading-snug text-ink-muted">{description}</p>
      <span
        className={`mono mt-auto pt-1 text-[8px] tracking-wider ${
          isShipped ? "text-ink-line" : "text-ink-faint"
        }`}
      >
        {isShipped ? "READY →" : "🚧 PLANNED"}
      </span>
    </div>
  );

  if (!isShipped) {
    return card;
  }
  return (
    <Link href={href} className="block">
      {card}
    </Link>
  );
}
