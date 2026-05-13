"use client";

import { useState } from "react";

import ScenarioForm from "@/components/actions/ScenarioForm";

const HOSTNAME_RE = /^[A-Za-z0-9-]{1,63}$/;

export default function ChangeHostnamePage() {
  const [hostname, setHostname] = useState("");
  const trimmed = hostname.trim();
  const valid = HOSTNAME_RE.test(trimmed);

  return (
    <ScenarioForm
      title="Change hostname (CLI)"
      description="Renames the router via SSH (`hostname <new>`). The agent will propose
        the change and wait for your approval on the Preview screen before
        actually sending the command."
      buildPrompt={() => `Change the hostname to ${trimmed}`}
      canSubmit={valid}
    >
      <label className="flex flex-col gap-1.5">
        <span className="mono text-[8px] tracking-wider text-ink-faint">
          NEW HOSTNAME
        </span>
        <input
          type="text"
          value={hostname}
          onChange={(e) => setHostname(e.target.value)}
          placeholder="LAB-R5"
          className="mono border border-rule bg-page px-2 py-1.5 text-[11px] text-ink placeholder:text-ink-line focus:border-ink focus:outline-none"
          autoFocus
          maxLength={63}
        />
        <span className="mono text-[8px] tracking-wider text-ink-line">
          Letters, digits, hyphens. Max 63 chars.
        </span>
      </label>
    </ScenarioForm>
  );
}
