// AI Configuration screen — chat + live event stream + sticky approve bar.

function ChatScreen({ pushPreview }) {
  const [messages, setMessages] = React.useState(INITIAL_CHAT);
  const [input, setInput] = React.useState("");
  const [typing, setTyping] = React.useState(false);
  const [pending, setPending] = React.useState(null); // current awaiting-approval proposal
  const [stream, setStream] = React.useState([]); // live event stream lines
  const [phase, setPhase] = React.useState("idle"); // idle | thinking | awaiting | executing | done
  const [history, setHistory] = React.useState([]);
  const scrollRef = React.useRef(null);

  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, typing]);

  // Auto-grow stream during certain phases
  const send = (text) => {
    if (!text.trim()) return;
    const userMsg = { role: "user", text };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setTyping(true);
    setPhase("thinking");

    // Pre-stream "thinking" lines
    const thinkingStream = [
      { line: "thinking · iter 0", kind: "think" },
      { line: "→ classify intent", kind: "tool" },
      { line: "→ search_docs", kind: "tool" },
      { line: "✓ search_docs", kind: "ok" },
      { line: "thinking · iter 1", kind: "think" },
    ];
    thinkingStream.forEach((s, i) =>
      setTimeout(() => setStream((p) => [...p, s]), 300 + i * 280)
    );

    const script = matchScript(text);
    const delay = script ? script.delay : 1400;

    setTimeout(() => {
      setTyping(false);
      if (!script) {
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            kind: "answer",
            text:
              "I'm not sure how to action that yet. Try one of these:\n\n- `add VLAN 30 named OFFICE`\n- `change hostname to LAB-R1`\n- `set GigabitEthernet0/1 to 192.168.10.1/24`\n- `add static route 10.99.99.0/24 via 192.168.10.254 via WebUI`\n- `how do I configure a trunk port?`",
          },
        ]);
        setStream((p) => [...p, { line: "✓ replied — no action proposed", kind: "ok" }]);
        setPhase("idle");
        return;
      }
      if (script.reply.kind === "answer") {
        setMessages((m) => [...m, { role: "assistant", kind: "answer", text: script.reply.text }]);
        setStream((p) => [...p, { line: "✓ replied — sources cited", kind: "ok" }]);
        setPhase("idle");
        return;
      }
      // proposal
      const proposal = script.reply;
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          kind: "proposal",
          proposal,
        },
      ]);
      setStream((p) => [
        ...p,
        { line: "→ propose_" + proposal.transport + "_configure", kind: "tool" },
        { line: "✓ propose_" + proposal.transport + "_configure", kind: "ok" },
        { line: "⏸ awaiting_approval · " + proposal.actionId, kind: "pause" },
      ]);
      setPending(proposal);
      setPhase("awaiting");
    }, delay);
  };

  const onApprove = () => {
    if (!pending) return;
    setStream((p) => [...p, { line: "✓ APPROVED · " + pending.actionId, kind: "ok" }]);
    setMessages((m) => [...m, { role: "system", text: "Approved " + pending.actionId + ". Click EXECUTE NOW to apply." }]);
  };
  const onReject = () => {
    if (!pending) return;
    setStream((p) => [...p, { line: "✗ REJECTED · " + pending.actionId, kind: "fail" }]);
    setMessages((m) => [...m, { role: "system", text: "Rejected " + pending.actionId + ". No router contact." }]);
    setPending(null);
    setPhase("idle");
  };
  const onExecute = () => {
    if (!pending) return;
    setPhase("executing");
    const events = buildExecuteStream(pending);
    events.forEach((e) =>
      setTimeout(() => setStream((p) => [...p, { line: e.line, kind: e.kind }]), e.t)
    );
    const totalDur = events[events.length - 1].t + 300;
    setTimeout(() => {
      setMessages((m) => [
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
      setHistory((h) => [{ ...pending, doneAt: new Date() }, ...h]);
      setPending(null);
      setPhase("done");
      setTimeout(() => setPhase("idle"), 1200);
    }, totalDur);
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
