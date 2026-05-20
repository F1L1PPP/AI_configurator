// AI Configuration screen — chat + live event stream + sticky approve bar.

// Builds a proposal object from a chat reply that has awaiting_approval set.
// Finds the last tool_call event whose name starts with "propose_" to extract
// commands, risk, transport, verify, affects, note, and params.
// Falls back gracefully if no matching tool_call is found in events.
function synthesizeProposal(reply) {
  const actionId = reply.awaiting_approval;
  const events = Array.isArray(reply.events) ? reply.events : [];

  // Find the last tool_call event whose name starts with "propose_"
  let lastToolCall = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "tool_call" && ev.data && typeof ev.data.name === "string" && ev.data.name.startsWith("propose_")) {
      lastToolCall = ev.data;
      break;
    }
  }

  // Defensive fallback — no matching tool_call found
  if (!lastToolCall) {
    return {
      actionId,
      summary: reply.final_text,
      risk: "low",
      transport: "cli",
      commands: [],
      verify: "",
      affects: "",
      note: "",
      existingEntity: null,
      existingBlock: null,
      isExactMatch: false,
    };
  }

  const toolName = lastToolCall.name || "";
  const transport = toolName.includes("webui") ? "webui" : "cli";
  const input = lastToolCall.input || {};

  // summary: prefer last awaiting_approval event's data.preview, else final_text
  // preview_meta: conflict-detection fields emitted as a dedicated key on the
  // awaiting_approval event (never on the tool_call input, which is the propose
  // tool's CALL args, not its RESULT).
  let summary = reply.final_text;
  let previewMeta = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "awaiting_approval" && ev.data) {
      if (ev.data.preview) summary = ev.data.preview;
      if (ev.data.preview_meta) previewMeta = ev.data.preview_meta;
      break;
    }
  }

  const commands = input.commands || (input.params && input.params.commands) || [];
  const risk = input.risk || "low";
  const verify = input.verify_pattern || "";
  const affects = input.affects || "";
  const note = input.note || "";
  const existingEntity = previewMeta && previewMeta.existing_entity || null;
  const existingBlock = previewMeta && previewMeta.existing_block || null;
  const isExactMatch = Boolean(previewMeta && previewMeta.is_exact_match);

  return { actionId, summary, risk, transport, commands, verify, affects, note, existingEntity, existingBlock, isExactMatch };
}

// Maps a backend WebSocket event ({type, ts, data}) to the {line, kind} shape
// the stream column renders. All 8 backend event types handled; unknown types
// get a defensive "info" fallback so one unknown event never crashes the render.
function adapterEventToStreamLine(ev) {
  const d = ev.data || {};
  switch (ev.type) {
    case "agent_thinking":
      return { line: "thinking · iter " + (d.iteration ?? "?"), kind: "think" };
    case "tool_call":
      return { line: "→ " + (d.name ?? ""), kind: "tool" };
    case "tool_result":
      return { line: "✓ " + (d.name ?? ""), kind: "ok" };
    case "awaiting_approval":
      return { line: "⏸ awaiting · " + (d.action_id ?? ""), kind: "pause" };
    case "applied":
      return { line: "✓ applied · " + (d.tool ?? ""), kind: "ok" };
    case "verified":
      return { line: "✓ verified", kind: "verify" };
    case "error":
      return { line: "✗ " + (d.message ?? ""), kind: "fail" };
    case "cli_command_sent": {
      // Per-command event from backend/cli_agent/write_tools.py (chunk 2b).
      // Renders like a terminal scroll: `(config)#` for `mode: "config"`,
      // `#` for `mode: "exec"` (post-write `show ...` verify reads).
      const prompt = d.mode === "exec" ? "#" : "(config)#";
      return { line: prompt + " " + (d.command ?? ""), kind: "cli" };
    }
    default:
      return { line: "· " + (ev.type || "unknown"), kind: "info" };
  }
}

function ChatScreen({ pushPreview }) {
  // Persisted chat state lives in window.ChatContext (provided by ChatProvider
  // in app.jsx) so navigating away and back keeps the conversation alive.
  // The WS subscription also lives at the provider, so live-stream events
  // that arrive while the user is on another page aren't dropped.
  const ctx = React.useContext(window.ChatContext);
  const {
    messages, setMessages,
    pending, setPending,
    stream,
    phase, setPhase,
    setHistory,
    chatHistory, setChatHistory,
    reset,
  } = ctx;

  // Input + typing + scroll are not worth persisting — they're transient
  // and would be confusing if they survived a page navigation.
  const [input, setInput] = React.useState("");
  const [typing, setTyping] = React.useState(false);
  const scrollRef = React.useRef(null);

  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, typing]);

  // Auto-grow stream during certain phases
  async function send(text) {
    if (!text.trim()) return;
    setMessages(m => [...m, { role: "user", text }]);
    setInput("");
    setTyping(true);
    setPhase("thinking");
    try {
      const reply = await window.api.sendChat(text, chatHistory);
      setChatHistory(reply.history);
      if (reply.awaiting_approval) {
        // Synthesize a proposal from reply.events for the existing proposal-bubble UI.
        const proposal = synthesizeProposal(reply);
        setMessages(m => [...m, { role: "assistant", kind: "proposal", proposal }]);
        setPending(proposal);
        setPhase("awaiting");
      } else {
        setMessages(m => [...m, { role: "assistant", kind: "answer", text: reply.final_text }]);
        setPhase("idle");
      }
    } catch (err) {
      setMessages(m => [...m, { role: "system", text: "Chat failed: " + (err?.message || String(err)) }]);
      setPhase("idle");
    } finally {
      setTyping(false);
    }
  }

  const onApprove = async () => {
    if (!pending) return;
    try {
      await window.api.approveAction(pending.actionId);
      setMessages(m => [...m, { role: "system", text: "Approved · " + pending.actionId }]);
      // Phase stays "awaiting" — user clicks Execute next.
    } catch (err) {
      setMessages(m => [...m, { role: "system", text: "Approve failed: " + (err?.message || String(err)) }]);
    }
  };
  const onReject = async () => {
    if (!pending) return;
    try {
      await window.api.rejectAction(pending.actionId);
      setMessages(m => [...m, { role: "system", text: "Rejected · " + pending.actionId }]);
    } catch (err) {
      setMessages(m => [...m, { role: "system", text: "Reject failed: " + (err?.message || String(err)) }]);
    } finally {
      setPending(null);
      setPhase("idle");
    }
  };
  const onExecute = async () => {
    if (!pending) return;
    setPhase("executing");
    try {
      await window.api.executeAction(pending.actionId);
      setMessages(m => [
        ...m,
        {
          role: "assistant",
          kind: "result",
          ok: true,
          actionId: pending.actionId,
          summary: pending.summary,
          verify: pending.verify,
        },
      ]);
      setHistory(h => [{ ...pending, doneAt: new Date() }, ...h]);
      setPending(null);
      setPhase("done");
      setTimeout(() => setPhase("idle"), 1200);
    } catch (err) {
      setMessages(m => [...m, { role: "system", text: "Execute failed: " + (err?.message || String(err)) }]);
      setPhase("idle");
    }
  };

  const onPushPreview = () => {
    if (pending) pushPreview(pending);
  };

  const suggestions = [
    "add VLAN 30 named OFFICE",
    "change hostname to LAB-R1",
    "set GigabitEthernet0/1 to 192.168.10.1/24",
    "how do I configure a trunk port?",
  ];

  return (
    <div className={"screen screen--ai" + (pending ? " has-sticky" : "")}>
      <div className="ai-mesh-bg" aria-hidden="true">
        <MeshScatter width={1200} height={400} count={70} opacity={0.45} />
      </div>
      <div className="ai-layout">
        {/* Conversation column */}
        <section className="chat-col">
          <div className="chat-head">
            <div className="chat-head-title">
              <span className="chat-dot" />
              Session SES-0042
            </div>
            <div className="chat-head-meta">
              <span>Router-01 · 192.168.1.1</span>
              <span className={"chat-phase chat-phase--" + phase}>
                {phase === "idle" && "Idle"}
                {phase === "thinking" && "Thinking…"}
                {phase === "awaiting" && "Awaiting approval"}
                {phase === "executing" && "Executing"}
                {phase === "done" && "Complete"}
              </span>
              <button
                type="button"
                className="chat-reset-btn"
                onClick={reset}
                disabled={typing || phase === "executing"}
                title="Clear messages, history, and live event stream"
              >
                Reset chat
              </button>
            </div>
          </div>

          <div className="chat-scroll" ref={scrollRef}>
            {messages.length <= 1 && !typing && (
              <div className="chat-empty-hero">
                <AnimatedGlobe size={240} meteors={2} interval={4500} thump={false} />
                <div className="chat-empty-title">Ask AI to configure your device</div>
                <div className="chat-empty-sub">
                  Type a plain-English request below, or pick a suggestion to get started.
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <Message key={i} m={m} />
            ))}
            {typing && (
              <div className="msg msg--assistant">
                <div className="msg-bubble msg-typing">
                  <span /><span /><span />
                </div>
              </div>
            )}
          </div>

          <div className="chat-input-wrap">
            <div className="chat-suggestions">
              {suggestions.map((s) => (
                <button key={s} className="sugg" onClick={() => send(s)} disabled={typing || phase === "executing"}>
                  {s}
                </button>
              ))}
            </div>
            <form
              className="chat-input"
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
            >
              <input
                type="text"
                value={input}
                placeholder="Ask AI to configure your device…"
                onChange={(e) => setInput(e.target.value)}
                disabled={typing || phase === "executing"}
              />
              <button type="submit" aria-label="Send" disabled={typing || phase === "executing"}>
                <IconSend />
              </button>
            </form>
          </div>
        </section>

        {/* Live event stream column */}
        <aside className="stream-col">
          <div className="stream-head">
            <div className="stream-title">LIVE EVENT STREAM</div>
            <div className="stream-sub">/ws/agent</div>
          </div>
          <div className="stream-body">
            {stream.length === 0 ? (
              <div className="stream-empty">
                <MeshSphere size={96} rotY={0.5} strokeWidth={0.4} />
                <p>Waiting for agent activity.</p>
              </div>
            ) : (
              stream.map((s, i) => (
                <div key={i} className={"stream-line stream-line--" + s.kind}>
                  {s.line}
                </div>
              ))
            )}
          </div>
          <div className="stream-foot">
            <div className="stream-stat">
              <span>Iterations</span>
              <b>{stream.filter((s) => s.kind === "think").length}</b>
            </div>
            <div className="stream-stat">
              <span>Tool calls</span>
              <b>{stream.filter((s) => s.kind === "tool").length}</b>
            </div>
            <div className="stream-stat">
              <span>Status</span>
              <b className={"phase phase--" + phase}>{phase}</b>
            </div>
          </div>
        </aside>
      </div>

      {/* Sticky approval bar */}
      {pending && (
        <div className={"approve-bar approve-bar--" + (phase === "executing" ? "executing" : "live")}>
          <div className="approve-bar-inner">
            <div className="ab-left">
              <div className="ab-meta">
                <span className={"risk risk--" + pending.risk}>{pending.risk.toUpperCase()} RISK</span>
                <span className="ab-action-id">{pending.actionId}</span>
                <span className="ab-transport">{pending.transport.toUpperCase()}</span>
              </div>
              <div className="ab-summary">{pending.summary}</div>
              <div className="ab-affects">{pending.affects}</div>
            </div>
            <div className="ab-right">
              <Btn kind="ghost" onClick={onPushPreview}>View diff</Btn>
              <Btn kind="danger" onClick={onReject} disabled={phase === "executing"} icon={<IconX />}>Reject</Btn>
              <Btn kind="outline" onClick={onApprove} disabled={phase === "executing"}>
                <IconCheck /> Approve
              </Btn>
              <Btn kind="primary" onClick={onExecute} disabled={phase === "executing"}>
                <IconPlay /> Execute now
              </Btn>
            </div>
          </div>
          <div className="approve-bar-note">
            Two-click safety contract · APPROVE then EXECUTE. The router is not touched until both are clicked.
          </div>
        </div>
      )}
    </div>
  );
}

function Message({ m }) {
  if (m.role === "user") {
    return (
      <div className="msg msg--user">
        <div className="msg-bubble">{m.text}</div>
      </div>
    );
  }
  if (m.role === "system") {
    return (
      <div className="msg msg--system">
        <div className="msg-bubble">— {m.text} —</div>
      </div>
    );
  }
  if (m.kind === "answer") {
    return (
      <div className="msg msg--assistant">
        <div className="msg-bubble">
          <MarkdownLite text={m.text} />
        </div>
      </div>
    );
  }
  if (m.kind === "proposal") {
    return <ProposalBubble proposal={m.proposal} />;
  }
  if (m.kind === "result") {
    return (
      <div className="msg msg--assistant">
        <div className="msg-bubble msg-result">
          <div className="result-head">
            <span className="result-tick"><IconCheck /></span>
            <div>
              <div className="result-title">Executed — {m.summary}</div>
              <div className="result-id">{m.actionId}</div>
            </div>
          </div>
          <div className="result-body">
            <div className="result-row"><span>Verify</span><code>{m.verify}</code></div>
            <div className="result-row"><span>Snapshots</span><code>artifacts/device-snapshots/{m.actionId}/</code></div>
          </div>
        </div>
      </div>
    );
  }
  return null;
}

function ProposalBubble({ proposal }) {
  return (
    <div className="msg msg--assistant">
      <div className="msg-bubble msg-proposal">
        <div className="prop-head">
          <span className="prop-eyebrow">PROPOSAL · {proposal.transport.toUpperCase()}</span>
          <span className="prop-id">{proposal.actionId}</span>
        </div>
        <div className="prop-summary">{proposal.summary}</div>
        <div className="prop-note">{proposal.note}</div>
        {proposal.existingEntity && (
          <div className={"prop-block prop-existing-block" + (proposal.isExactMatch ? " prop-existing-block--noop" : "")}>
            <div className="prop-block-title">
              {proposal.isExactMatch
                ? "IDENTICAL CONFIG — APPLYING WILL BE A NO-OP"
                : "REPLACES EXISTING — " + proposal.existingEntity}
            </div>
            <pre className="codeblock">{proposal.existingBlock}</pre>
            {proposal.isExactMatch && (
              <div className="prop-existing-noop-hint">
                Approve to confirm the redundant write, or reject to cancel.
              </div>
            )}
          </div>
        )}
        <div className="prop-block">
          <div className="prop-block-title">
            {proposal.transport === "cli" ? "IOS XE commands" : "WebUI steps"}
          </div>
          <pre className="codeblock">
            {proposal.commands.map((c, i) => (
              <div key={i} className="code-line">
                <span className="code-num">{String(i + 1).padStart(2, "0")}</span>
                <span>{c}</span>
              </div>
            ))}
          </pre>
        </div>
        <div className="prop-block">
          <div className="prop-block-title">Verify</div>
          <code className="codeline">{proposal.verify}</code>
        </div>
        <div className="prop-tag">
          See sticky bar below to APPROVE / EXECUTE.
        </div>
      </div>
    </div>
  );
}

// Tiny markdown renderer — handles paragraphs, bold, inline code, code fences, bullet lists
function MarkdownLite({ text }) {
  const lines = text.split("\n");
  const out = [];
  let inCode = false;
  let codeBuf = [];
  let listBuf = [];
  const flushList = () => {
    if (listBuf.length) {
      out.push(<ul key={"ul" + out.length}>{listBuf.map((l, i) => <li key={i}><Inline t={l} /></li>)}</ul>);
      listBuf = [];
    }
  };
  lines.forEach((ln, i) => {
    if (ln.startsWith("```")) {
      flushList();
      if (inCode) {
        out.push(<pre key={"c" + i} className="codeblock"><code>{codeBuf.join("\n")}</code></pre>);
        codeBuf = [];
      }
      inCode = !inCode;
      return;
    }
    if (inCode) {
      codeBuf.push(ln);
      return;
    }
    if (ln.startsWith("- ")) {
      listBuf.push(ln.slice(2));
      return;
    }
    flushList();
    if (!ln.trim()) {
      out.push(<div key={"sp" + i} className="md-sp" />);
      return;
    }
    out.push(<p key={"p" + i}><Inline t={ln} /></p>);
  });
  flushList();
  if (codeBuf.length) out.push(<pre key="cend" className="codeblock"><code>{codeBuf.join("\n")}</code></pre>);
  return <div className="md">{out}</div>;
}

function Inline({ t }) {
  // **bold** and `code`
  const parts = [];
  let rest = t;
  let key = 0;
  while (rest.length) {
    const b = rest.indexOf("**");
    const c = rest.indexOf("`");
    const next = [b, c].filter((x) => x >= 0).sort((x, y) => x - y)[0];
    if (next === undefined) {
      parts.push(rest);
      break;
    }
    if (next > 0) parts.push(rest.slice(0, next));
    if (next === b) {
      const end = rest.indexOf("**", b + 2);
      if (end < 0) { parts.push(rest.slice(b)); break; }
      parts.push(<b key={key++}>{rest.slice(b + 2, end)}</b>);
      rest = rest.slice(end + 2);
    } else {
      const end = rest.indexOf("`", c + 1);
      if (end < 0) { parts.push(rest.slice(c)); break; }
      parts.push(<code key={key++}>{rest.slice(c + 1, end)}</code>);
      rest = rest.slice(end + 1);
    }
  }
  return <>{parts}</>;
}

Object.assign(window, { ChatScreen });
