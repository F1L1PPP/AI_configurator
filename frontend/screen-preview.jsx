// Configuration Preview screen — CLI diff + change summary + apply

function PreviewScreen({ preview }) {
  const [autoActionId, setAutoActionId] = React.useState(null);
  const [action, setAction] = React.useState(null);

  // When no preview prop is supplied (direct navigation), fetch the most-recent
  // action via last-backup so the screen always shows something real.
  React.useEffect(() => {
    if (!preview?.actionId) {
      window.api.fetchLastBackup("router-01").then(function (r) {
        setAutoActionId((r && r.action_id) ? r.action_id : null);
      });
    }
  }, [preview?.actionId]);

  const effectiveActionId = preview?.actionId || autoActionId;

  React.useEffect(() => {
    setAction(null);
    if (effectiveActionId) {
      window.api.fetchPreview(effectiveActionId).then(setAction);
    }
  }, [effectiveActionId]);

  // Branch A: waiting for an action_id to resolve (initial load or auto-fetch pending)
  // Branch B: action loaded AND before/after populated — real diff (post-execute)
  // Branch C: action loaded but no snapshots yet — pre-execute fallback

  const hasDiff = action && action.before && action.before.length > 0 && action.after && action.after.length > 0;
  const isLoading = !action && effectiveActionId;

  // Derive header fields from the loaded action, never from hardcoded demo data.
  const risk = (action && action.action && action.action.params && action.action.params.risk) ? action.action.params.risk : "low";
  const tool = (action && action.action && action.action.tool) ? action.action.tool : "";
  const transport = tool.startsWith("webui_") ? "webui" : (tool ? "cli" : "cli");
  const displayActionId = effectiveActionId || "—";
  const summary = tool
    ? (tool + (action.action.params && action.action.params.hostname ? " → " + action.action.params.hostname : ""))
    : "Most recent action";
  const commands = action
    ? (action.commands && action.commands.length ? action.commands
       : ((action.action && action.action.params && action.action.params.config_commands) ? action.action.params.config_commands : []))
    : [];
  const verifyPattern = (action && action.action && action.action.params && action.action.params.verify_pattern)
    ? action.action.params.verify_pattern
    : "";

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
            <span className={"risk risk--" + risk}>{risk.toUpperCase()} RISK</span>
            <span className="ph-transport">{transport.toUpperCase()}</span>
            <span className="ph-id">{displayActionId}</span>
          </div>
          <h1 className="ph-title">{summary}</h1>
        </div>
        <div className="ph-right">
          <Btn kind="danger" icon={<IconArrowLeft />} onClick={function () { console.warn("[preview] Back not wired"); }}>Back</Btn>
          <Btn kind="outline" icon={<IconCheck />} onClick={function () { console.warn("[preview] Approve not wired"); }}>Approve</Btn>
          <Btn kind="primary" icon={<IconPlay />} onClick={function () { console.warn("[preview] Apply config not wired"); }}>Apply config</Btn>
        </div>
      </div>

      <div className="preview-grid">
        {renderBeforeCard()}
        {renderAfterCard()}
      </div>

      <div className="preview-grid">
        <Card title="Change summary" className="card--summary">
          {commands.length > 0 ? (
            <ul className="summary-list">
              {commands.map(function (c, i) {
                return (
                  <li key={i}><span className="sig sig--add">+</span> <code>{c}</code></li>
                );
              })}
            </ul>
          ) : (
            <div className="muted">See Commands card for details.</div>
          )}
          {verifyPattern && (
            <div className="prop-tag">
              Verify after apply: <code>{verifyPattern}</code>
            </div>
          )}
        </Card>

        <Card title="Commands to execute" className="card--commands">
          {commands.length > 0 ? (
            <pre className="codeblock">
              {commands.map((c, i) => (
                <div key={i} className="code-line">
                  <span className="code-num">{String(i + 1).padStart(2, "0")}</span>
                  <span>{c}</span>
                </div>
              ))}
            </pre>
          ) : (
            <div className="muted">No commands recorded for this action.</div>
          )}
        </Card>
      </div>
      <div className="preview-foot-wave">
        <InteractiveMeshWave height={180} />
      </div>
    </div>
  );
}

Object.assign(window, { PreviewScreen });
