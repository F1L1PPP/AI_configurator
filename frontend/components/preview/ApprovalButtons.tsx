"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { endpoints } from "@/lib/api";
import {
  describeNetworkError,
  fromResponse,
  type FriendlyError,
} from "@/lib/errors";

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
  /** Network timeout in ms for approve/reject. Execute uses a separate
   *  longer cap (`executeTimeoutMs`) because Playwright flows are slow. */
  timeoutMs?: number;
  /** Hard cap on the EXECUTING state. If the backend doesn't respond by
   *  this time the UI flips to error so the operator isn't staring at a
   *  frozen "EXECUTING…" forever. Review §3 HIGH. The fetch keeps
   *  running — we only flip UI state, so a late success will still log
   *  to the LiveEventStream + Logs. */
  executeTimeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 5000;
const DEFAULT_EXECUTE_TIMEOUT_MS = 90_000;

export default function ApprovalButtons({
  actionId,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  executeTimeoutMs = DEFAULT_EXECUTE_TIMEOUT_MS,
}: Props) {
  const [state, setState] = useState<BtnState>("idle");
  const [friendlyError, setFriendlyError] = useState<
    (FriendlyError & { detail?: string }) | null
  >(null);
  const [executeResult, setExecuteResult] = useState<unknown>(null);
  // Track an in-flight request so a second click while the first hasn't
  // resolved doesn't fire a duplicate POST. Ref instead of state because
  // we don't want a re-render between click and guard.
  const inFlight = useRef(false);
  // Hold the EXECUTING-timeout id so we can clear it if the fetch
  // resolves normally — otherwise the timer would still fire and
  // overwrite the success state.
  const executeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup the execute timer on unmount so a navigation away from this
  // bubble doesn't leave a dangling setState call.
  useEffect(() => {
    return () => {
      if (executeTimerRef.current !== null) {
        clearTimeout(executeTimerRef.current);
      }
    };
  }, []);

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
    setFriendlyError(null);

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
        setFriendlyError(await fromResponse(res));
        setState("error");
      }
    } catch (err) {
      setFriendlyError(describeNetworkError(err));
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
    setFriendlyError(null);
    setExecuteResult(null);

    // EXECUTING timeout cap — the previous code parked indefinitely; if
    // Playwright hung or SSH stalled the button showed "EXECUTING…"
    // forever. Now we flip to error after executeTimeoutMs so the
    // operator sees a clear failure state and can go check logs/. We
    // do NOT abort the underlying fetch — a late success still
    // mark_executed's the action server-side; we just stop blocking
    // the UI on a request we've given up on.
    executeTimerRef.current = setTimeout(() => {
      setFriendlyError({
        title: "Execution timed out",
        hint: `No response from the backend after ${Math.round(
          executeTimeoutMs / 1000,
        )}s. Open the WebUI Live tab or check the most recent logs/ entries. The action may still be running on the router — verify with \`show running-config\` before retrying.`,
      });
      setState("error");
      inFlight.current = false;
    }, executeTimeoutMs);

    try {
      const res = await fetch(endpoints.execute(actionId), { method: "POST" });
      // The fetch resolved — drop the watchdog timer so the timeout
      // doesn't fire and overwrite the result we're about to render.
      if (executeTimerRef.current !== null) {
        clearTimeout(executeTimerRef.current);
        executeTimerRef.current = null;
      }
      if (res.ok) {
        const body = await res.json();
        setExecuteResult(body);
        setState("executed");
      } else {
        setFriendlyError(await fromResponse(res));
        setState("error");
      }
    } catch (err) {
      if (executeTimerRef.current !== null) {
        clearTimeout(executeTimerRef.current);
        executeTimerRef.current = null;
      }
      setFriendlyError(describeNetworkError(err));
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
          watch the live event stream below for progress. Auto-cancels after{" "}
          {Math.round(executeTimeoutMs / 1000)}s.
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

      {state === "error" && friendlyError && (
        <div className="border border-terminal-red bg-surface p-2">
          <p className="mono text-[9px] font-semibold tracking-wider text-terminal-red">
            ✗ {friendlyError.title.toUpperCase()}
          </p>
          <p className="mono mt-1 text-[9px] leading-relaxed text-ink-muted">
            {friendlyError.hint}
          </p>
          {friendlyError.detail && (
            <details className="mono mt-1 text-[8px] text-ink-faint">
              <summary className="cursor-pointer tracking-wider">DETAIL</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words">
                {friendlyError.detail}
              </pre>
            </details>
          )}
        </div>
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
