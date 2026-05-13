"use client";

import { useState } from "react";

import ScenarioForm from "@/components/actions/ScenarioForm";

export default function AddVlanPage() {
  const [vlanId, setVlanId] = useState("");
  const [vlanName, setVlanName] = useState("");

  const idNum = Number(vlanId);
  const idValid =
    vlanId.trim().length > 0 &&
    Number.isInteger(idNum) &&
    idNum >= 1 &&
    idNum <= 4094;
  const nameValid = /^[A-Za-z0-9_-]{1,32}$/.test(vlanName.trim());
  const canSubmit = idValid && nameValid;

  return (
    <ScenarioForm
      title="Add access VLAN (WebUI)"
      description="Adds a VLAN through the WebUI in Playwright — opens a Chromium window
        you can watch. Every step is screenshotted into
        artifacts/screenshots/. After Save, the agent verifies via CLI
        `show vlan brief` that the row landed in the VLAN database."
      buildPrompt={() =>
        `Add VLAN ${vlanId.trim()} named ${vlanName.trim()} via the WebUI on the C1111.`
      }
      canSubmit={canSubmit}
    >
      <label className="flex flex-col gap-1.5">
        <span className="mono text-[8px] tracking-wider text-ink-faint">VLAN ID</span>
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={4094}
          value={vlanId}
          onChange={(e) => setVlanId(e.target.value)}
          placeholder="30"
          className="mono border border-rule bg-page px-2 py-1.5 text-[11px] text-ink placeholder:text-ink-line focus:border-ink focus:outline-none"
          autoFocus
        />
        <span className="mono text-[8px] tracking-wider text-ink-line">
          1–4094. Router validates the actual range.
        </span>
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="mono text-[8px] tracking-wider text-ink-faint">VLAN NAME</span>
        <input
          type="text"
          value={vlanName}
          onChange={(e) => setVlanName(e.target.value)}
          placeholder="OFFICE"
          maxLength={32}
          className="mono border border-rule bg-page px-2 py-1.5 text-[11px] text-ink placeholder:text-ink-line focus:border-ink focus:outline-none"
        />
        <span className="mono text-[8px] tracking-wider text-ink-line">
          Letters, digits, underscore, hyphen. Max 32 chars.
        </span>
      </label>
    </ScenarioForm>
  );
}
