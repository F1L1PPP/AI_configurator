"use client";

import { useState } from "react";

import ScenarioForm from "@/components/actions/ScenarioForm";

const IPV4_RE = /^(\d{1,3}\.){3}\d{1,3}$/;
const INTERFACE_RE = /^[A-Za-z][\w/.-]*$/;

export default function SetInterfaceIpPage() {
  const [iface, setIface] = useState("");
  const [ip, setIp] = useState("");
  const [mask, setMask] = useState("");

  const ifaceValid = INTERFACE_RE.test(iface.trim());
  const ipValid = IPV4_RE.test(ip.trim());
  const maskValid = IPV4_RE.test(mask.trim());
  const canSubmit = ifaceValid && ipValid && maskValid;

  return (
    <ScenarioForm
      title="Set interface IP (CLI)"
      description="Assigns an IPv4 address + mask to a routed interface and brings it
        up (`no shutdown`). Will propose first — APPROVE and EXECUTE NOW
        on the next screen run it for real."
      buildPrompt={() =>
        `Set the IP address ${ip.trim()} with mask ${mask.trim()} on interface ${iface.trim()} via CLI on the C1111.`
      }
      canSubmit={canSubmit}
    >
      <label className="flex flex-col gap-1.5">
        <span className="mono text-[8px] tracking-wider text-ink-faint">
          INTERFACE
        </span>
        <input
          type="text"
          value={iface}
          onChange={(e) => setIface(e.target.value)}
          placeholder="GigabitEthernet0/0/0"
          className="mono border border-rule bg-page px-2 py-1.5 text-[11px] text-ink placeholder:text-ink-line focus:border-ink focus:outline-none"
          autoFocus
        />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="mono text-[8px] tracking-wider text-ink-faint">IP</span>
          <input
            type="text"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
            placeholder="10.0.0.1"
            className="mono border border-rule bg-page px-2 py-1.5 text-[11px] text-ink placeholder:text-ink-line focus:border-ink focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="mono text-[8px] tracking-wider text-ink-faint">MASK</span>
          <input
            type="text"
            value={mask}
            onChange={(e) => setMask(e.target.value)}
            placeholder="255.255.255.0"
            className="mono border border-rule bg-page px-2 py-1.5 text-[11px] text-ink placeholder:text-ink-line focus:border-ink focus:outline-none"
          />
        </label>
      </div>
    </ScenarioForm>
  );
}
