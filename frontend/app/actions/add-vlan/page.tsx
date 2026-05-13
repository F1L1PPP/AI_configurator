"use client";

import { useState } from "react";

import ScenarioForm from "@/components/actions/ScenarioForm";

type Path = "cli" | "webui";

export default function AddVlanPage() {
  const [vlanId, setVlanId] = useState("");
  const [vlanName, setVlanName] = useState("");
  const [path, setPath] = useState<Path>("cli");

  const idNum = Number(vlanId);
  const idValid =
    vlanId.trim().length > 0 &&
    Number.isInteger(idNum) &&
    idNum >= 1 &&
    idNum <= 4094;
  const nameValid = /^[A-Za-z0-9_-]{1,32}$/.test(vlanName.trim());
  const canSubmit = idValid && nameValid;

  const buildPrompt = () => {
    if (path === "webui") {
      return `Add VLAN ${vlanId.trim()} named ${vlanName.trim()} via the WebUI on the C1111.`;
    }
    return `Add VLAN ${vlanId.trim()} named ${vlanName.trim()} via CLI on the C1111.`;
  };

  return (
    <ScenarioForm
      title="Add access VLAN"
      description="Creates a VLAN in the device's VLAN database. Pick the path: CLI is
        fast (~1 s via SSH); WebUI is slower (~25 s) but opens a Chromium
        window with screenshot evidence the demo evaluator can verify."
      buildPrompt={buildPrompt}
      canSubmit={canSubmit}
    >
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mono text-[8px] tracking-wider text-ink-faint">
          PATH
        </legend>
        <div className="flex gap-2">
          <label
            className={`mono flex flex-1 cursor-pointer items-center gap-2 border px-3 py-2 text-[10px] tracking-wide ${
              path === "cli"
                ? "border-ink bg-ink text-surface"
                : "border-rule bg-surface text-ink-muted hover:border-ink hover:text-ink"
            }`}
          >
            <input
              type="radio"
              name="path"
              value="cli"
              checked={path === "cli"}
              onChange={() => setPath("cli")}
              className="sr-only"
            />
            <span>CLI</span>
            <span className="mono text-[8px] tracking-wider opacity-60">
              fast · SSH
            </span>
          </label>
          <label
            className={`mono flex flex-1 cursor-pointer items-center gap-2 border px-3 py-2 text-[10px] tracking-wide ${
              path === "webui"
                ? "border-ink bg-ink text-surface"
                : "border-rule bg-surface text-ink-muted hover:border-ink hover:text-ink"
            }`}
          >
            <input
              type="radio"
              name="path"
              value="webui"
              checked={path === "webui"}
              onChange={() => setPath("webui")}
              className="sr-only"
            />
            <span>WebUI</span>
            <span className="mono text-[8px] tracking-wider opacity-60">
              slow · screenshots
            </span>
          </label>
        </div>
      </fieldset>

      <label className="flex flex-col gap-1.5">
        <span className="mono text-[8px] tracking-wider text-ink-faint">VLAN ID</span>
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={4094}
          value={vlanId}
          onChange={(e) => setVlanId(e.target.value)}
          placeholder="40"
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
