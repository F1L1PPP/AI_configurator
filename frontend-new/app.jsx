// Top-level App + Tweaks integration

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

  const deviceCount = MOCK_DEVICES.length;

  return (
    <>
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
    </>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
