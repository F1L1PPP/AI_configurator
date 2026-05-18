// Configuration Preview screen — CLI diff + change summary + apply

function PreviewScreen({ preview }) {
  const proposal = preview || {
    summary: "Add VLAN 30 named OFFICE on Router-01",
    actionId: "act_20260518_8e18fd",
    risk: "low",
    transport: "cli",
    commands: ["configure terminal", "vlan 30", " name OFFICE", " exit", "end", "write memory"],
    verify: "show vlan brief | include OFFICE",
    affects: "Router-01 (192.168.1.1) · interface allocation: GigabitEthernet0/1",
    note: "Adds a new L2 VLAN. Existing traffic is not affected until an interface is moved into this VLAN.",
  };

  const [action, setAction] = React.useState(null);

  React.useEffect(() => {
    setAction(null);
    if (preview?.actionId) {
      window.api.fetchPreview(preview.actionId).then(setAction);
    }
  }, [preview?.actionId]);

  // Branch A: preview exists but action not loaded yet — loading state
  // Branch B: action loaded AND before/after populated — real diff (post-execute)
  // Branch C: action loaded but no snapshots yet — pre-execute fallback

  const hasDiff = action && action.before && action.before.length > 0 && action.after && action.after.length > 0;
  const isLoading = !action && preview?.actionId;

  function renderBeforeCard() {
    if (isLoading) {
      return (
        <Card title="Running config — before" className="card--diff">
          <div className="muted">Loading…</div>
        </Card>
      );
    }
    if (hasDiff) {
      return (
        <Card title="Running config — before" className="card--diff">
          <pre className="diff diff--before">
            {action.before.map((ln, i) => (
              <div key={i} className="diff-line"><span className="diff-num">{i + 1}</span><span>{ln}</span></div>
            ))}
          </pre>
        </Card>
      );
    }
    // Branch C — no snapshot yet
    return (
      <Card title="Running config — before" className="card--diff">
        <div className="muted">No diff snapshot available — execute to see before/after.</div>
      </Card>
    );
  }

  function renderAfterCard() {
    if (isLoading) {
      return (
        <Card title="Running config — after" className="card--diff">
          <div className="muted">Loading…</div>
        </Card>
      );
    }
    if (hasDiff) {
      const addedSet = action.addedSet instanceof Set ? action.addedSet : new Set(action.addedSet || []);
      return (
        <Card title="Running config — after" className="card--diff">
          <pre className="diff diff--after">
            {action.after.map((ln, i) => {
              const added = addedSet.has(ln);
              return (
                <div key={i} className={"diff-line" + (added ? " diff-add" : "")}>
                  <span className="diff-num">{i + 1}</span>
                  <span className="diff-sign">{added ? "+" : " "}</span>
                  <span>{ln}</span>
                </div>
              );
            })}
          </pre>
        </Card>
      );
    }
    // Branch C — no snapshot yet
    return (
      <Card title="Running config — after" className="card--diff">
        <div className="muted">No diff snapshot available — execute to see before/after.</div>
      </Card>
    );
  }

  return (
    <div className="screen screen--preview">
      <div className="preview-head">
        <div className="ph-left">
          <div className="ph-eyebrow">
            <span className={"risk risk--" + proposal.risk}>{proposal.risk.toUpperCase()} RISK</span>
            <span className="ph-transport">{proposal.transport.toUpperCase()}</span>
            <span className="ph-id">{proposal.actionId}</span>
          </div>
          <h1 className="ph-title">{proposal.summary}</h1>
          <div className="ph-affects">{proposal.affects}</div>
        </div>
        <div className="ph-right">
          <Btn kind="danger" icon={<IconArrowLeft />}>Back</Btn>
          <Btn kind="outline" icon={<IconCheck />}>Approve</Btn>
          <Btn kind="primary" icon={<IconPlay />}>Apply config</Btn>
        </div>
      </div>

      <div className="preview-grid">
        {renderBeforeCard()}
        {renderAfterCard()}
      </div>

      <div className="preview-grid">
        <Card title="Change summary" className="card--summary">
          <ul className="summary-list">
            <li><span className="sig sig--add">+</span> <code>vlan 30</code></li>
            <li className="indent"><span className="sig sig--add">+</span> <code>name OFFICE</code></li>
          </ul>
          <div className="prop-tag">
            Verify after apply: <code>{proposal.verify}</code>
          </div>
        </Card>

        <Card title="Commands to execute" className="card--commands">
          <pre className="codeblock">
            {proposal.commands.map((c, i) => (
              <div key={i} className="code-line">
                <span className="code-num">{String(i + 1).padStart(2, "0")}</span>
                <span>{c}</span>
              </div>
            ))}
          </pre>
        </Card>
      </div>
      <div className="preview-foot-wave">
        <InteractiveMeshWave height={180} />
      </div>
    </div>
  );
}

Object.assign(window, { PreviewScreen });
