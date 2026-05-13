"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { endpoints } from "@/lib/api";

type BtnState =
  | "idle"
  | "loading"
  | "approved"
  | "rejected"
  | "executing"
  | "executed"
  | "error";

interface Props {
  actionId?: string;
  /** Network timeout in ms for approve/reject. Execute has no timeout
   *  because WebUI flows can run 20-30s while Playwright drives. */
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 5000;

export default function ApprovalButtons({
  actionId,
  timeoutMs = DEFAULT_TIMEOUT_MS,
}: Props) {
  const [state, setState] = useState<BtnState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [executeResult, setExecuteResult] = useState<unknown>(null);
  // Track an in-flight request so a second click while the first hasn't
  // resolved doesn't fire a duplicate POST. Ref instead of state because
  // we don't want a re-render between click and guard.
  const inFlight = useRef(false);

  const approveDisabled =
    !actionId ||
    state === "loading" ||
    state === "approved" ||
    state === "rejected" ||
    state === "executing" ||
    state === "executed";

  const rejectDisabled =
    !actionId ||
    state === "loading" ||
    state === "approved" ||
    state === "rejected" ||
    state === "executing" ||
    state === "executed";

  // Execute Now button is visible from approval onward — including during
  // execution (showing "EXECUTING…") and on error (so the user can retry).
  // Including "executing" in the union keeps TS happy with the label switch.
  const canExecute =
    !!actionId &&
    (state === "approved" ||
      state === "executing" ||
      (state === "error" && !!executeResult));

  const post = async (endpoint: "approve" | "reject") => {
    if (!actionId) return;
    if (
      inFlight.current ||
      state === "loading" ||
      state === "approved" ||
      state === "rejected" ||
      state === "executing" ||
      state === "executed"
    ) {
      return;
    }
    inFlight.current = true;
    setState("loading");
    setErrorMsg(null);

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

  const runExecute = async () => {
    if (!actionId || !canExecute || inFlight.current) return;
    inFlight.current = true;
    setState("executing");
    setErrorMsg(null);
    setExecuteResult(null);

    // No AbortController — WebUI flows can take 20-30s while Playwright
    // drives the browser; we don't want to abort mid-flow.
    try {
      const res = await fetch(endpoints.execute(actionId), { method: "POST" });
      if (res.ok) {
        const body = await res.json();
        setExecuteResult(body);
        setState("executed");
      } else {
        const body = await res.text().catch(() => res.statusText);
        setErrorMsg(body || `HTTP ${res.status}`);
        setState("error");
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Network error");
      setState("error");
    } finally {
      inFlight.current = false;
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-3">
        <button
          disabled={approveDisabled}
          onClick={() => post("approve")}
          className="mono flex-1 border border-ink bg-ink px-4 py-2.5 text-[9px] tracking-wider text-surface hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {state === "loading"
            ? "APPROVING…"
            : state === "approved" || state === "executing" || state === "executed"
              ? "✓ APPROVED"
              : "APPROVE"}
        </button>
        <button
          disabled={rejectDisabled}
          onClick={() => post("reject")}
          className="mono flex-1 border border-ink px-4 py-2.5 text-[9px] tracking-wider text-ink hover:bg-page disabled:cursor-not-allowed disabled:opacity-40"
        >
          {state === "rejected" ? "✗ REJECTED" : "REJECT"}
        </button>
      </div>

      {canExecute && (
        <button
          disabled={state === "executing"}
          onClick={runExecute}
          className="mono border-2 border-ink bg-surface px-4 py-2.5 text-[10px] tracking-wider text-ink hover:bg-ink hover:text-surface disabled:cursor-not-allowed disabled:opacity-40"
        >
          {state === "executing" ? "EXECUTING — DON'T CLOSE THIS TAB…" : "▶ EXECUTE NOW"}
        </button>
      )}

      {state === "executing" && (
        <p className="mono text-[8px] tracking-wider text-ink-faint">
          Backend is running the tool. WebUI flows open a Chromium window;
          watch the live event stream below for progress.
        </p>
      )}

      {state === "executed" && (
        <div className="flex flex-col gap-1.5 border border-ink bg-page p-2">
          <p className="mono text-[9px] tracking-wider text-ink">
            ✓ EXECUTED — action complete
          </p>
          {executeResult ? (
            <details className="mono text-[8px] text-ink-muted">
              <summary className="cursor-pointer tracking-wider">RESULT</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words">
                {JSON.stringify(executeResult, null, 2)}
              </pre>
            </details>
          ) : null}
          <Link
            href="/chat"
            className="mono text-[8px] tracking-wider text-ink-line underline hover:text-ink"
          >
            → BACK TO CHAT
          </Link>
        </div>
      )}

      {state === "error" && errorMsg && (
        <p className="mono text-[8px] tracking-wider text-terminal-red">
          ERROR: {errorMsg}
        </p>
      )}

      {!actionId && (
        <p className="mono text-[8px] tracking-wider text-ink-faint">
          NO ACTION ID — add ?action_id=act_… to the URL, or start from{" "}
          <Link href="/chat" className="underline">/chat</Link> /{" "}
          <Link href="/actions" className="underline">/actions</Link>.
        </p>
      )}

      {state === "approved" && (
        <p className="mono text-[8px] tracking-wider text-ink-faint">
          ✓ AUTHORISED — click EXECUTE NOW above to run the action, or
          return to <Link href="/chat" className="underline">/chat</Link>{" "}
          and say "execute it".
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
