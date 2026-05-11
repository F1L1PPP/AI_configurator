type Msg = {
  id: string;
  role: "user" | "agent" | "tool";
  text: string;
  time: string;
  thinking?: boolean;
};

const mockConversation: Msg[] = [
  {
    id: "m1",
    role: "user",
    text: "Show me the running interfaces and the device version.",
    time: "10:32",
  },
  {
    id: "m2",
    role: "agent",
    text: "I'll run two read-only commands over SSH. No approval needed for reads.",
    time: "10:32",
  },
  {
    id: "m3",
    role: "tool",
    text: "→ cli_show_ip_interface_brief()  ✓  3 interfaces up",
    time: "10:32",
  },
  {
    id: "m4",
    role: "tool",
    text: "→ cli_show_version()  ✓  IOS XE 17.09.4a",
    time: "10:32",
  },
  {
    id: "m5",
    role: "agent",
    text:
      "GigabitEthernet0/0/0 is up (192.168.1.1/24), Gi0/0/1 is up (10.0.0.1/24), " +
      "Vlan1 is administratively down. The C1111 is running IOS XE 17.09.4a (Bengaluru). " +
      "Anything else?",
    time: "10:32",
  },
  {
    id: "m6",
    role: "user",
    text: "Change the hostname to LAB-R1 using the WebUI.",
    time: "10:34",
  },
  {
    id: "m7",
    role: "agent",
    text:
      "I'll use the WebUI agent (Playwright). This is a write action — I've " +
      "prepared a plan. Review it on the Preview screen before I execute.",
    time: "10:34",
  },
  {
    id: "m8",
    role: "agent",
    text: "Waiting for your approval…",
    time: "10:34",
    thinking: true,
  },
];

const Bubble = ({ msg }: { msg: Msg }) => {
  if (msg.role === "tool") {
    return (
      <div className="mono py-1 pl-12 pr-4 text-[10px] text-ink-muted">{msg.text}</div>
    );
  }
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 px-1 py-2 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`mono flex h-6 w-6 shrink-0 items-center justify-center border text-[9px] tracking-wider ${
          isUser ? "border-ink bg-ink text-surface" : "border-ink text-ink"
        }`}
      >
        {isUser ? "U" : "AI"}
      </div>
      <div className={`flex max-w-[70%] flex-col gap-1 ${isUser ? "items-end" : ""}`}>
        <div
          className={`border px-3 py-2 text-[11px] leading-relaxed ${
            isUser ? "border-ink bg-ink text-surface" : "border-rule bg-surface text-ink"
          }`}
        >
          {msg.thinking ? (
            <span className="inline-flex items-center gap-2">
              <span className="mono inline-flex gap-0.5">
                <span className="inline-block h-1 w-1 animate-pulse rounded-full bg-current" />
                <span
                  className="inline-block h-1 w-1 animate-pulse rounded-full bg-current"
                  style={{ animationDelay: "150ms" }}
                />
                <span
                  className="inline-block h-1 w-1 animate-pulse rounded-full bg-current"
                  style={{ animationDelay: "300ms" }}
                />
              </span>
              {msg.text}
            </span>
          ) : (
            msg.text
          )}
        </div>
        <span className="mono text-[8px] tracking-wider text-ink-line">{msg.time}</span>
      </div>
    </div>
  );
};

export default function ChatPage() {
  return (
    <div className="flex h-[calc(100vh-90px)] flex-col gap-3">
      <section className="flex-1 overflow-y-auto border border-rule bg-surface p-4">
        <div className="mono mb-3 border-b border-rule-soft pb-2 text-[8px] tracking-wider text-ink-faint">
          SESSION 2026-05-11 · 10:32 · 8 MESSAGES · MOCKED — DAY 6 WIRES WS
        </div>
        {mockConversation.map((m) => (
          <Bubble key={m.id} msg={m} />
        ))}
      </section>

      <section className="border border-rule bg-surface">
        <div className="flex items-center gap-2 px-3 py-2">
          <span className="mono text-[8px] tracking-wider text-ink-line">$</span>
          <input
            type="text"
            placeholder="Ask the agent — e.g. add VLAN 30 named OFFICE on Gi0/0/1"
            disabled
            className="mono flex-1 bg-transparent text-[11px] text-ink placeholder:text-ink-line focus:outline-none"
          />
          <button
            disabled
            className="mono border border-ink bg-ink px-3 py-1 text-[8px] tracking-wider text-surface disabled:opacity-50"
          >
            SEND
          </button>
        </div>
      </section>

      <div className="mono text-[8px] tracking-wider text-ink-faint">
        DAY 1: INPUT DISABLED · DAY 6: WIRED TO POST /CHAT + WS /WS/AGENT
      </div>
    </div>
  );
}
