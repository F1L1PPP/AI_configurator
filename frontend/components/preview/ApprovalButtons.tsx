"use client";

import { useState } from "react";

import { API_BASE } from "@/lib/api";

type BtnState = "idle" | "loading" | "approved" | "rejected" | "error";

interface Props {
  actionId?: string;
}

export default function ApprovalButtons({ actionId }: Props) {
  const [state, setState] = useState<BtnState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const disabled = !actionId || state === "loading" || state === "approved" || state === "rejected";

  const post = async (endpoint: "approve" | "reject") => {
    if (!actionId) return;
    setState("loading");
    setErrorMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/${endpoint}/${actionId}`, {
        method: "POST",
      });
      if (res.ok) {
        setState(endpoint === "approve" ? "approved" : "rejected");
      } else {
        const body = await res.text().catch(() => res.statusText);
        setErrorMsg(body);
        setState("error");
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Network error");
      setState("error");
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
            ? "EXECUTING…"
            : state === "approved"
              ? "✓ APPROVED"
              : "APPROVE & EXECUTE"}
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

      {actionId && state === "idle" && (
        <p className="mono text-[8px] tracking-wider text-ink-faint">
          ACTION: {actionId}
        </p>
      )}
    </div>
  );
}
