import ScenarioCard from "@/components/actions/ScenarioCard";

export default function ActionsIndexPage() {
  return (
    <div className="flex flex-col gap-4">
      <header className="border border-rule bg-surface px-3.5 py-3">
        <div className="tech-label mb-1">All scenarios</div>
        <p className="text-[10px] leading-relaxed text-ink-muted">
          The 6 §2 scenarios the agent can do today. Each card opens a form
          with the right inputs; submit, approve on the next screen, the
          agent executes. The full chat (RAG + WebSocket + Sources) is at{" "}
          <a href="/chat" className="mono text-ink underline">/chat</a>.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3">
        <ScenarioCard
          title="01 — Change hostname (CLI)"
          description="Rename the router via SSH. Fast path; ~1 s end-to-end."
          href="/actions/change-hostname"
          status="shipped"
          badge="CLI"
        />
        <ScenarioCard
          title="02 — Set interface IP (CLI)"
          description="Assign an IPv4 address + mask to a Gi interface."
          href="/actions/set-interface-ip"
          status="shipped"
          badge="CLI"
        />
        <ScenarioCard
          title="03 — Change hostname (WebUI)"
          description="Drive the WebUI in Playwright; produces screenshots."
          href="/chat"
          status="shipped"
          badge="WebUI"
        />
        <ScenarioCard
          title="04 — Add access VLAN (WebUI)"
          description="Add a VLAN with ID + Name via the WebUI; CLI verifies."
          href="/actions/add-vlan"
          status="shipped"
          badge="WebUI"
        />
        <ScenarioCard
          title="05 — Show running-config / interfaces"
          description="Read-only queries — ask via the chat for now."
          href="/chat"
          status="shipped"
          badge="CLI"
        />
        <ScenarioCard
          title="06 — Ask the docs (RAG)"
          description="Semantic search over the curated Cisco corpus with citations."
          href="/chat"
          status="shipped"
          badge="RAG"
        />
      </div>
    </div>
  );
}
