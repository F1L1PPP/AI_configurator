"use client";

import { useRef, useState } from "react";

import { endpoints } from "@/lib/api";

type BtnState = "idle" | "loading" | "approved" | "rejected" | "error";

interface Props {
  actionId?: string;
  /** Network timeout in ms before we abort the in-flight request. */
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 5000;

export default function ApprovalButtons({
  actionId,
  timeoutMs = DEFAULT_TIMEOUT_MS,
}: Props) {
  const [state, setState] = useState<BtnState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Track an in-flight request so a second click while the first hasn't
  // resolved doesn't fire a duplicate POST (audit #14). Ref instead of
  // state because we don't want a re-render between click and guard.
  const inFlight = useRef(false);

  const disabled =
    !actionId ||
    state === "loading" ||
    state === "approved" ||
    state === "rejected";

  const post = async (endpoint: "approve" | "reject") => {
    if (!actionId) return;
    // Double-click guard — return early if a request is already in the air,
    // even if state hasn't updated yet (React batches setState).
    if (inFlight.current || state !== "idle") return;
    inFlight.current = true;
    setState("loading");
    setErrorMsg(null);

    // AbortController + setTimeout — without this, a hung backend leaves
    // the UI stuck on "APPROVING…" indefinitely.
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const url =
        endpoint === "approve"
          ? endpoints.approve(actionId)
          : endpoints.reject(actionId);
      const res = await fetch(url, {
        method: "POST",
        signal: controller.signal,
      });
      if (res.ok) {
        setState(endpoint === "approve" ? "approved" : "rejected");
      } else {
        const body = await res.text().catch(() => res.statusText);
        setErrorMsg(body || `HTTP ${res.status}`);
        setState("error");
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setErrorMsg(`Request timed out after ${timeoutMs} ms`);
      } else {
        setErrorMsg(err instanceof Error ? err.message : "Network error");
      }
      setState("error");
    } finally {
      clearTimeout(timeout);
      inFlight.current = false;
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-3">
        <button
          disabled={disabled}
          onClick={() => post("approve")}
          className="mono flex-1 border border-ink bg-ink px-4 py-2.5 text-[9px] tracking-wider text-surface hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {state === "loading"
            ? "APPROVING…"
            : state === "approved"
              ? "✓ APPROVED"
              : "APPROVE"}
        </button>
        <button
          disabled={disabled}
          onClick={() => post("reject")}
          className="mono flex-1 border border-ink px-4 py-2.5 text-[9px] tracking-wider text-ink hover:bg-page disabled:cursor-not-allowed disabled:opacity-40"
        >
          {state === "rejected" ? "✗ REJECTED" : "REJECT"}
        </button>
      </div>

      {state === "error" && errorMsg && (
        <p className="mono text-[8px] tracking-wider text-terminal-red">
          ERROR: {errorMsg}
        </p>
      )}

      {!actionId && (
        <p className="mono text-[8px] tracking-wider text-ink-faint">
          NO ACTION ID — add ?action_id=act_… to the URL
        </p>
      )}

      {state === "approved" && (
        <p className="mono text-[8px] tracking-wider text-ink-faint">
          ✓ AUTHORISED — return to chat and tell the agent to execute
        </p>
      )}

      {actionId && state === "idle" && (
        <p className="mono text-[8px] tracking-wider text-ink-faint">
          ACTION: {actionId}
        </p>
      )}
    </div>
  );
}
