// Chrome: AppShell, Sidebar, TopBar, Card, Button, Pill, etc.

// ---- Icons (defined first so NAV array can reference them) ---------------

function strokeIcon(d, { width = 18, height = 18 } = {}) {
  return function Icon({ className = "", style }) {
    return (
      <svg
        viewBox="0 0 24 24"
        width={width}
        height={height}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        style={style}
        aria-hidden="true">
        
        {d}
      </svg>);

  };
}

const IconHome = strokeIcon(
  <>
    <path d="M3 11.5 12 4l9 7.5" />
    <path d="M5 10v10h14V10" />
  </>
);
const IconDevices = strokeIcon(
  <>
    <rect x="3" y="6" width="18" height="12" rx="1.5" />
    <path d="M7 10h2M7 14h2M11 10h2M11 14h2M15 10h2M15 14h2" />
  </>
);
const IconChat = strokeIcon(
  <>
    <path d="M4 5h16v11H8l-4 4z" />
    <path d="M8 9h8M8 12h5" />
  </>
);
const IconDiff = strokeIcon(
  <>
    <path d="M7 4v10M7 14a3 3 0 0 0 3 3h2" />
    <path d="M17 20V10M17 10a3 3 0 0 0-3-3h-2" />
    <circle cx="7" cy="4" r="1.6" />
    <circle cx="17" cy="20" r="1.6" />
  </>
);
const IconBrowser = strokeIcon(
  <>
    <rect x="3" y="5" width="18" height="14" rx="1.5" />
    <path d="M3 9h18" />
    <circle cx="6" cy="7" r="0.8" />
    <circle cx="8.5" cy="7" r="0.8" />
  </>
);
const IconCog = strokeIcon(
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" />
  </>
);
const IconSend = strokeIcon(
  <>
    <path d="M4 12 20 4l-4 16-4-7-8-1z" />
  </>
);
const IconPlus = strokeIcon(
  <>
    <path d="M12 5v14M5 12h14" />
  </>
);
const IconCheck = strokeIcon(
  <>
    <path d="m5 12 5 5 9-11" />
  </>
);
const IconX = strokeIcon(
  <>
    <path d="m6 6 12 12M18 6 6 18" />
  </>
);
const IconPlay = strokeIcon(
  <>
    <path d="M6 4v16l14-8z" />
  </>
);
const IconArrowLeft = strokeIcon(
  <>
    <path d="M19 12H5" />
    <path d="M12 5l-7 7 7 7" />
  </>
);
const IconUndo = strokeIcon(
  <>
    <path d="M3 7v6h6" />
    <path d="M3 13a9 9 0 1 0 3-7" />
  </>
);
const IconDot = strokeIcon(<circle cx="12" cy="12" r="3" fill="currentColor" />);

// ---- App shell ------------------------------------------------------------

function AppShell({ route, setRoute, children, deviceCount, connected }) {
  return (
    <div className="app-shell">
      <Sidebar route={route} setRoute={setRoute} />
      <div className="app-main">
        <TopBar route={route} deviceCount={deviceCount} connected={connected} />
        <div className="app-content">{children}</div>
      </div>
    </div>);

}

const NAV = [
{ id: "dashboard", label: "Dashboard", icon: IconHome },
{ id: "devices", label: "Devices", icon: IconDevices },
{ id: "ai", label: "AI Configuration", icon: IconChat },
{ id: "preview", label: "Config Preview", icon: IconDiff },
{ id: "settings", label: "Settings", icon: IconCog }];


function Sidebar({ route, setRoute }) {
  const [device, setDevice] = React.useState(null);
  React.useEffect(() => {
    window.api.fetchDevices().then((rows) => {
      if (rows && rows.length) setDevice(rows[0]);
    });
  }, []);

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="brand-mark">
          <EthernetCableLogo size={28} className="brand-mark-logo" />
        </div>
        <div className="brand">
          <div className="brand-title">CISCO AI CONFIG</div>
          <div className="brand-sub">AI-POWERED NETWORK CONFIGURATION</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV.map((n) => {
          const Icon = n.icon;
          const active = route === n.id;
          return (
            <button
              key={n.id}
              type="button"
              className={"nav-item" + (active ? " is-active" : "")}
              onClick={() => setRoute(n.id)}
              aria-current={active ? "page" : undefined}>

              <Icon className="nav-icon" />
              <span className="nav-label">{n.label}</span>
              {active && <span className="nav-active-bar" aria-hidden="true" />}
            </button>);

        })}
      </nav>

      <div className="sidebar-foot">
        <div className="active-device-card">
          <div className="adc-label">ACTIVE DEVICE</div>
          <div className="adc-name">{device ? device.name : "—"}</div>
          <div className="adc-ip">{device ? device.ip : "—"}</div>
          <div className="adc-art" aria-hidden="true" />
          <div className="adc-ios">{device ? device.ios : "—"}</div>
          <div className="adc-status">
            <span className={"dot dot--" + (device && device.status === "connected" ? "ok" : "warn")} />
            {device ? device.status : "loading"}
          </div>
        </div>
      </div>
    </aside>);

}

function TopBar({ route, deviceCount, connected }) {
  const titles = {
    dashboard: "Dashboard",
    devices: "Devices",
    ai: "AI Configuration",
    preview: "Configuration Preview",
    settings: "Settings"
  };
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="route-crumb">{titles[route] || ""}</div>
      </div>
      <div className="topbar-right">
        <div className="agent-status">
          <span className="status-label">AGENT STATUS</span>
          <span className={"status-pill " + (connected ? "is-on" : "is-off")}>
            <span className="dot" style={{ backgroundColor: "rgb(91, 229, 72)" }} />
            {connected ? "Active" : "Idle"}
          </span>
        </div>
        <div className="device-count">
          <span className="device-count-num">{deviceCount}</span>
          <span className="device-count-lbl">DEVICES</span>
        </div>
      </div>
    </header>);

}

// ---- Reusable bits --------------------------------------------------------

function Card({ children, className = "", title, action, decoration, style }) {
  return (
    <section className={"card " + className} style={style}>
      {(title || action) &&
      <header className="card-head">
          {title && <div className="card-title">{title}</div>}
          {action && <div className="card-action">{action}</div>}
        </header>
      }
      <div className="card-body">{children}</div>
      {decoration}
    </section>);

}

function Btn({ children, kind = "primary", size = "md", icon, onClick, disabled, type = "button", className = "", style }) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`btn btn--${kind} btn--${size} ${className}`}
      style={style}>
      
      {icon && <span className="btn-icon">{icon}</span>}
      <span>{children}</span>
    </button>);

}

function Pill({ children, kind = "neutral", icon }) {
  return (
    <span className={`pill pill--${kind}`}>
      {icon && <span className="pill-icon">{icon}</span>}
      {children}
    </span>);

}

function Field({ label, hint, children, required }) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {required && <em className="field-req"> *</em>}
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>);

}

function Input(props) {
  return <input {...props} className={"input " + (props.className || "")} />;
}

// ---- Icons exported via window assign below ------------------------------

Object.assign(window, {
  AppShell,
  Sidebar,
  TopBar,
  Card,
  Btn,
  Pill,
  Field,
  Input,
  IconHome,
  IconDevices,
  IconChat,
  IconDiff,
  IconBrowser,
  IconCog,
  IconSend,
  IconPlus,
  IconCheck,
  IconX,
  IconPlay,
  IconDot,
  IconArrowLeft,
  IconUndo,
});