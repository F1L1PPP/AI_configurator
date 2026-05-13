"use client";

import { useState } from "react";

import ScenarioForm from "@/components/actions/ScenarioForm";

const HOSTNAME_RE = /^[A-Za-z0-9-]{1,63}$/;

type Path = "cli" | "webui";

export default function ChangeHostnamePage() {
  const [hostname, setHostname] = useState("");
  const [path, setPath] = useState<Path>("cli");
  const trimmed = hostname.trim();
  const valid = HOSTNAME_RE.test(trimmed);

  // The planner's system prompt rule 3 selects the WebUI path when the
  // message contains "via WebUI" / "v prehliadači" / similar. Otherwise
  // it defaults to CLI. So we just put the right phrase into the prompt.
  const buildPrompt = () => {
    if (path === "webui") {
      return `Change the hostname to ${trimmed} via the WebUI on the C1111.`;
    }
    return `Change the hostname to ${trimmed} via CLI on the C1111.`;
  };

  return (
    <ScenarioForm
      title="Change hostname"
      description="Renames the router. Pick the path: CLI is fast (~1 s end-to-end via
        SSH); WebUI is slower (~25 s) but opens a Chromium window with
        screenshot evidence."
      buildPrompt={buildPrompt}
      canSubmit={valid}
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
