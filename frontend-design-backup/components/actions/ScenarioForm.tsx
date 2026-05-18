"use client";

import { useRouter } from "next/navigation";
import { FormEvent, ReactNode, useState } from "react";

import { endpoints } from "@/lib/api";

interface ScenarioFormProps {
  title: string;
  description: string;
  /** Build the natural-language prompt sent to /api/chat from the form values. */
  buildPrompt: () => string;
  /** Inputs (controlled by parent — keep state up there). */
  children: ReactNode;
  /** Disable submit when the parent's form state is incomplete. */
  canSubmit: boolean;
}

/**
 * Shared shell for /actions/* form pages.
 *
 * Each scenario page renders its inputs (children) and supplies a
 * `buildPrompt()` that turns them into the prompt string. On submit we
 * POST to /api/chat (the same endpoint the chat box uses), wait for the
 * planner to return an `awaiting_approval` action_id, then redirect to
 * /preview?action_id=... so the user can approve and watch execution.
 *
 * If the planner doesn't surface an action_id (e.g. it just answered
 * the question without proposing a write), we surface the final_text
 * inline instead.
 */
export default function ScenarioForm({
  title,
  description,
  buildPrompt,
  children,
  canSubmit,
}: ScenarioFormProps) {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setError(null);
    setReply(null);
    try {
      const prompt = buildPrompt();
      const res = await fetch(endpoints.chat(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: prompt, history: [] }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
      }
      const body = (await res.json()) as {
        final_text: string;
        awaiting_approval: string | null;
      };
      if (body.awaiting_approval) {
        // Redirect to /preview with the action_id pre-loaded — operator clicks
        // APPROVE, then returns to chat (or sees it in event stream) to execute.
        router.push(`/preview?action_id=${encodeURIComponent(body.awaiting_approval)}`);
        return;
      }
      setReply(body.final_text || "(agent replied without proposing an action)");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="border border-rule bg-surface px-3.5 py-3">
        <div className="tech-label mb-1">{title}</div>
        <p className="text-[10px] leading-relaxed text-ink-muted">{description}</p>
      </header>

      <form
        onSubmit={onSubmit}
        className="flex flex-col gap-3 border border-rule bg-surface p-4"
      >
        {children}

        <div className="flex items-center gap-2 pt-1">
          <button
            type="submit"
            disabled={!canSubmit || submitting}
            className="mono border border-ink bg-ink px-4 py-1.5 text-[9px] tracking-wider text-surface disabled:opacity-50"
          >
            {submitting ? "PROPOSING…" : "PROPOSE → APPROVE"}
          </button>
          <span className="mono text-[8px] tracking-wider text-ink-faint">
            POSTs to /api/chat → redirect to /preview on success
          </span>
        </div>

        {error && (
          <p className="mono text-[9px] tracking-wider text-terminal-red">
            ERROR: {error}
          </p>
        )}
        {reply && (
          <pre className="mono whitespace-pre-wrap border border-rule-soft bg-page p-2 text-[10px] text-ink">
            {reply}
          </pre>
        )}
      </form>
    </div>
  );
}
