// Top-level App + Tweaks integration

// ---------------------------------------------------------------------------
// Chat persistence (chunk 2 of the 2026-05-19 roadmap)
//
// All chat state lives in a Context that wraps the App, so navigating
// between Dashboard / Devices / AI Configuration / Preview doesn't unmount
// the conversation. The WebSocket subscription also lives here — events
// that arrive while the user is on another page still accumulate into the
// live stream instead of being dropped on every unmount/remount cycle.
//
// The Reset chat button in screen-ai.jsx calls `reset()` on this context.
// ---------------------------------------------------------------------------

const CHAT_STREAM_MAX_LINES = 200;
const ChatContext = React.createContext(null);
window.ChatContext = ChatContext;

function ChatProvider({ children }) {
  const [messages, setMessages] = React.useState([]);
  const [pending, setPending] = React.useState(null);
  const [stream, setStream] = React.useState([]);
  const [phase, setPhase] = React.useState("idle");
  const [history, setHistory] = React.useState([]);
  const [chatHistory, setChatHistory] = React.useState([]);

  // Pacing queue for cli_command_sent events. The backend emits all
  // commands in a tight burst right before send_config_set (which is a
  // single SSH round-trip). For a terminal-scroll feel, we drain the
  // queue one line every 120 ms instead of dumping the burst at once.
  // Other event types render immediately — pacing applies only to
  // cli_command_sent.
  const cliQueueRef = React.useRef({ items: [], timer: null });
  const CLI_PACING_MS = 120;

  const appendStreamLine = React.useCallback((line) => {
    setStream((s) => {
      const next = [...s, line];
      return next.length > CHAT_STREAM_MAX_LINES ? next.slice(-CHAT_STREAM_MAX_LINES) : next;
    });
  }, []);

  const drainCliQueue = React.useCallback(() => {
    const q = cliQueueRef.current;
    if (q.timer || q.items.length === 0) return;
    q.timer = setTimeout(() => {
      const next = q.items.shift();
      if (next) appendStreamLine(next);
      q.timer = null;
      drainCliQueue();
    }, CLI_PACING_MS);
  }, [appendStreamLine]);

  // WS subscription lives at app lifetime, not per-page. `adapterEventToStreamLine`
  // is a top-level function in screen-ai.jsx and becomes a global at script load.
  React.useEffect(() => {
    if (typeof window.api === "undefined" || typeof adapterEventToStreamLine !== "function") {
      return;
    }
    const handle = window.api.connectAgentWs((ev) => {
      const line = adapterEventToStreamLine(ev);
      if (ev.type === "cli_command_sent") {
        cliQueueRef.current.items.push(line);
        drainCliQueue();
      } else {
        appendStreamLine(line);
      }
    });
    return () => handle.close();
  }, [appendStreamLine, drainCliQueue]);

  const reset = React.useCallback(() => {
    // Drain any in-flight cli_command_sent pacing queue so reset is truly
    // clean — otherwise queued lines would appear AFTER the user clicked
    // reset and confuse them.
    const q = cliQueueRef.current;
    if (q.timer) {
      clearTimeout(q.timer);
      q.timer = null;
    }
    q.items = [];
    setMessages([]);
    setPending(null);
    setStream([]);
    setPhase("idle");
    setHistory([]);
    setChatHistory([]);
  }, []);

  const value = {
    messages, setMessages,
    pending, setPending,
    stream, setStream,
    phase, setPhase,
    history, setHistory,
    chatHistory, setChatHistory,
    reset,
  };
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

const DEFAULTS = /*EDITMODE-BEGIN*/{
  "dark": false,
  "accent": "#1e6cff",
  "meshIntensity": 50
}/*EDITMODE-END*/;

const ACCENT_OPTIONS = [
  "#1e6cff", // tech blue (default)
  "#22a06b", // network green
  "#d97757", // copper
  "#7c3aed", // violet
  "#e5484d", // alert red
];

function App() {
  const [route, setRoute] = React.useState("dashboard");
  const [tweaks, setTweak] = useTweaks(DEFAULTS);
  const [previewProposal, setPreviewProposal] = React.useState(null);

  // Theme + accent application
  React.useEffect(() => {
    document.documentElement.classList.toggle("theme-dark", !!tweaks.dark);
    document.documentElement.style.setProperty("--accent", tweaks.accent);
    // derive accent-soft (alpha)
    const hex = tweaks.accent || "#1e6cff";
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    document.documentElement.style.setProperty(
      "--accent-soft",
      `rgba(${r}, ${g}, ${b}, ${tweaks.dark ? 0.22 : 0.12})`
    );
    document.documentElement.style.setProperty(
      "--accent-ink",
      tweaks.dark ? "#07090c" : "#ffffff"
    );
  }, [tweaks.dark, tweaks.accent]);

  const pushPreview = (p) => {
    setPreviewProposal(p);
    setRoute("preview");
  };

  const [deviceCount, setDeviceCount] = React.useState(0);
  React.useEffect(() => {
    window.api.fetchDevices().then(d => setDeviceCount(d.length));
  }, []);

  return (
    <ChatProvider>
      <div className="bg-mesh">
        <MeshScatter width={1600} height={1200} count={tweaks.meshIntensity ? Math.round(40 + tweaks.meshIntensity * 0.8) : 0} opacity={1} />
      </div>
      <AppShell route={route} setRoute={setRoute} deviceCount={deviceCount} connected={true}>
        {route === "dashboard" && (
          <DashboardScreen
            onGotoChat={() => setRoute("ai")}
            onGotoDevices={() => setRoute("devices")}
          />
        )}
        {route === "devices" && <DevicesScreen />}
        {route === "ai" && <ChatScreen pushPreview={pushPreview} />}
        {route === "preview" && <PreviewScreen preview={previewProposal} />}
        {route === "settings" && <SettingsScreen tweaks={tweaks} setTweak={setTweak} />}
      </AppShell>

      <TweaksPanel title="Tweaks">
        <TweakSection title="Theme">
          <TweakRadio
            label="Mode"
            value={tweaks.dark ? "dark" : "light"}
            onChange={(v) => setTweak("dark", v === "dark")}
            options={[
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
            ]}
          />
          <TweakColor
            label="Accent"
            value={tweaks.accent}
            onChange={(v) => setTweak("accent", v)}
            options={ACCENT_OPTIONS}
          />
        </TweakSection>

        <TweakSection title="Mesh visuals">
          <TweakSlider
            label="Background density"
            value={tweaks.meshIntensity}
            min={0}
            max={100}
            step={10}
            onChange={(v) => setTweak("meshIntensity", v)}
          />
        </TweakSection>

        <TweakSection title="Jump to">
          <TweakButton onClick={() => setRoute("ai")}>AI Chat</TweakButton>
          <TweakButton onClick={() => setRoute("preview")}>Config Preview</TweakButton>
        </TweakSection>
      </TweaksPanel>
    </ChatProvider>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
