// Dashboard + Devices + Settings screens

function DashboardScreen({ onGotoChat, onGotoDevices }) {
  const [activity, setActivity] = React.useState(window.RECENT_ACTIVITY || []);
  React.useEffect(() => {
    window.api.fetchRecentActivity(10).then(rows => {
      if (rows.length) setActivity(rows);
    });
  }, []);

  return (
    <div className="screen screen--dashboard">
      <div className="dash-intro">
        <div>
          <div className="dash-eyebrow">SESSION · SES-0042</div>
          <h1 className="dash-title">Dashboard</h1>
          <p className="dash-desc">
            AI-powered configuration for your Cisco routers. Type what you want changed —
            every write is gated by APPROVE / EXECUTE before it touches the device.
          </p>
        </div>
        <div className="dash-intro-actions">
          <Btn kind="ghost" onClick={onGotoDevices} icon={<IconPlus />}>
            Connect device
          </Btn>
          <Btn kind="primary" onClick={onGotoChat} icon={<IconChat />}>
            Open AI Configuration
          </Btn>
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi">
          <div className="kpi-num">1</div>
          <div className="kpi-lbl">Device connected</div>
        </div>
        <div className="kpi">
          <div className="kpi-num">3</div>
          <div className="kpi-lbl">Active sessions</div>
        </div>
        <div className="kpi">
          <div className="kpi-num">12</div>
          <div className="kpi-lbl">Configs saved</div>
        </div>
        <div className="kpi kpi--health">
          <div>
            <div className="kpi-health-status">
              <Pill kind="ok"><span className="dot" /> Good</Pill>
            </div>
            <div className="kpi-lbl">Health</div>
          </div>
        </div>
      </div>

      <div className="dash-grid">
        <Card title="Recent activity" className="card--activity">
          <ul className="activity">
            {activity.map((a) => (
              <li key={a.id} className="activity-item">
                <span className={`act-dot act-dot--${a.kind}`} />
                <div className="activity-body">
                  <div className="activity-text">{a.text}</div>
                  <div className="activity-id">{a.id}</div>
                </div>
                <div className="activity-time">{a.time}</div>
              </li>
            ))}
          </ul>
        </Card>

        <div className="dash-right-col">
          <Card title="Device overview" className="card--device-overview">
            <div className="device-overview">
              <div className="device-overview-art" aria-hidden="true" />
              <div className="device-overview-info">
                <div className="dov-name">Router-01</div>
                <div className="dov-ip">192.168.1.1</div>
                <div className="dov-meta">IOS XE 17.6.1 · ISR 4321</div>
                <div className="dov-stats">
                  <div><span>Uptime</span><b>23d 14h</b></div>
                  <div><span>Sessions</span><b>3 active</b></div>
                  <div><span>Last backup</span><b>06m ago</b></div>
                </div>
              </div>
            </div>
          </Card>

          <Card title="Quick actions" className="card--quick-actions">
            <div className="quick-actions">
              <button className="qa-btn">
                <span className="qa-icon"><IconCheck /></span>
                <span className="qa-text">
                  <span className="qa-name">Backup config</span>
                  <span className="qa-sub">Snapshot running-config to disk</span>
                </span>
              </button>
              <button className="qa-btn">
                <span className="qa-icon"><IconDevices /></span>
                <span className="qa-text">
                  <span className="qa-name">Show interfaces</span>
                  <span className="qa-sub">List all GE / VLAN ports</span>
                </span>
              </button>
              <button className="qa-btn">
                <span className="qa-icon"><IconDot /></span>
                <span className="qa-text">
                  <span className="qa-name">Ping test</span>
                  <span className="qa-sub">Reachability check</span>
                </span>
              </button>
              <button className="qa-btn" onClick={onGotoChat}>
                <span className="qa-icon"><IconChat /></span>
                <span className="qa-text">
                  <span className="qa-name">Ask AI…</span>
                  <span className="qa-sub">Open AI Configuration</span>
                </span>
              </button>
            </div>
          </Card>

          <Card title="Connection trace" className="card--mini-stream">
            <ul className="mini-stream">
              <li><span className="ms-time">10:24</span><span className="ms-ok">●</span><span>handshake ok</span></li>
              <li><span className="ms-time">10:24</span><span className="ms-ok">●</span><span>ssh authenticated</span></li>
              <li><span className="ms-time">10:24</span><span className="ms-ok">●</span><span>config sync · 12 files</span></li>
              <li><span className="ms-time">10:23</span><span className="ms-ok">●</span><span>vlan brief · 3 entries</span></li>
            </ul>
          </Card>
        </div>
      </div>

      <div className="dash-wave">
        <InteractiveMeshWave height={240} />
      </div>
    </div>
  );
}

// ---------- Devices screen ----------

function DevicesScreen() {
  const [devices, setDevices] = React.useState([]);
  React.useEffect(() => {
    window.api.fetchDevices().then(setDevices);
  }, []);

  const [form, setForm] = React.useState({ ip: "192.168.1.4", user: "admin", pass: "", port: "443" });
  const [connecting, setConnecting] = React.useState(false);
  const [connected, setConnected] = React.useState(false);

  const onConnect = (e) => {
    e.preventDefault();
    setConnecting(true);
    setTimeout(() => {
      setConnecting(false);
      setConnected(true);
      setTimeout(() => setConnected(false), 2200);
    }, 1400);
  };

  return (
    <div className="screen screen--devices">
      <div className="devices-grid">
        <Card title="Add new device" className="card--add-device">
          <form className="form" onSubmit={onConnect}>
            <Field label="Device IP" required>
              <Input
                type="text"
                value={form.ip}
                onChange={(e) => setForm({ ...form, ip: e.target.value })}
              />
            </Field>
            <Field label="Username" required>
              <Input
                type="text"
                value={form.user}
                onChange={(e) => setForm({ ...form, user: e.target.value })}
              />
            </Field>
            <Field label="Password" required>
              <Input
                type="password"
                value={form.pass}
                placeholder="••••••••"
                onChange={(e) => setForm({ ...form, pass: e.target.value })}
              />
            </Field>
            <Field label="Port (optional)">
              <Input
                type="text"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: e.target.value })}
              />
            </Field>
            <div className="form-actions">
              <Btn kind="primary" type="submit" disabled={connecting}>
                {connecting ? "Connecting…" : connected ? "Connected ✓" : "Connect"}
              </Btn>
            </div>
            {connecting && (
              <div className="connect-stream">
                <div>→ ssh-handshake</div>
                <div>→ probing IOS XE…</div>
                <div>→ enumerating interfaces</div>
              </div>
            )}
          </form>
        </Card>

        <Card className="card--add-art">
          <div className="add-art">
            <MeshSphere size={260} rotY={0.6} rotX={-0.15} strokeWidth={0.55} />
            <div className="add-art-hint">
              Connects over SSH or HTTPS WebUI. The agent only reads at first —
              no writes until you approve them.
            </div>
          </div>
        </Card>
      </div>

      <Card title="Connected devices" className="card--device-list">
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>IP address</th>
              <th>Model</th>
              <th>IOS</th>
              <th>Uptime</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {!devices.length ? (
              <tr>
                <td colSpan={7} className="muted">Loading devices…</td>
              </tr>
            ) : devices.map((d) => (
              <tr key={d.id}>
                <td>
                  <div className="dev-cell">
                    <span className="dev-cell-sphere">
                      <MeshSphere size={28} rotY={0.4} strokeWidth={0.4} dots={false} />
                    </span>
                    <span>{d.name}</span>
                  </div>
                </td>
                <td className="mono">{d.ip}</td>
                <td>{d.model}</td>
                <td className="mono">{d.ios}</td>
                <td className="mono">{d.uptime}</td>
                <td>
                  <Pill kind={d.status === "connected" ? "ok" : "warn"}>
                    <span className="dot" />
                    {d.status}
                  </Pill>
                </td>
                <td>
                  <div className="row-actions">
                    <button className="row-btn" aria-label="Configure">Configure</button>
                    <button className="row-btn" aria-label="Disconnect">Disconnect</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ---------- Settings screen ----------

function SettingsScreen({ tweaks, setTweak }) {
  const accentOptions = ["#1e6cff", "#22a06b", "#d97757", "#7c3aed", "#e5484d"];
  return (
    <div className="screen screen--settings">
      <div className="settings-grid">
        <Card title="Appearance" className="card--settings">
          <div className="setting-row">
            <div>
              <div className="tog-title">Theme</div>
              <div className="tog-sub">Switch between light and dark mode</div>
            </div>
            <div className="theme-switch">
              <button
                type="button"
                className={"theme-opt" + (!tweaks.dark ? " is-active" : "")}
                onClick={() => setTweak("dark", false)}
              >
                Light
              </button>
              <button
                type="button"
                className={"theme-opt" + (tweaks.dark ? " is-active" : "")}
                onClick={() => setTweak("dark", true)}
              >
                Dark
              </button>
            </div>
          </div>
          <div className="setting-row">
            <div>
              <div className="tog-title">Accent color</div>
              <div className="tog-sub">Used for active states, links, live indicators</div>
            </div>
            <div className="accent-swatches">
              {accentOptions.map((c) => (
                <button
                  key={c}
                  type="button"
                  className={"accent-swatch" + (tweaks.accent === c ? " is-active" : "")}
                  style={{ "--swatch": c }}
                  onClick={() => setTweak("accent", c)}
                  aria-label={"Accent " + c}
                />
              ))}
            </div>
          </div>
        </Card>

        <Card title="Agent" className="card--settings">
          <Field label="Backend URL">
            <Input defaultValue="http://localhost:8000" />
          </Field>
          <Field label="WebSocket URL">
            <Input defaultValue="ws://localhost:8000/ws/agent" />
          </Field>
          <Field label="Default transport">
            <select className="input">
              <option>CLI (SSH)</option>
              <option>WebUI (Chromium)</option>
              <option>Let the agent choose</option>
            </select>
          </Field>
        </Card>

        <Card title="Safety" className="card--settings">
          <div className="toggle-row">
            <div>
              <div className="tog-title">Require APPROVE before EXECUTE</div>
              <div className="tog-sub">Two-click contract. Cannot be disabled.</div>
            </div>
            <span className="lock-toggle">LOCKED</span>
          </div>
          <div className="toggle-row">
            <div>
              <div className="tog-title">Auto-snapshot before write</div>
              <div className="tog-sub">Saves running-config to artifacts/.</div>
            </div>
            <span className="lock-toggle is-on">ENABLED</span>
          </div>
          <div className="toggle-row">
            <div>
              <div className="tog-title">Open Chromium during WebUI flows</div>
              <div className="tog-sub">Visible agent actions build trust.</div>
            </div>
            <span className="lock-toggle is-on">ENABLED</span>
          </div>
        </Card>

        <Card title="About" className="card--settings card--about">
          <div className="about-art">
            <EthernetCableLogo size={64} strokeWidth={0.7} />
          </div>
          <div>
            <div className="about-title">CISCO AI CONFIG</div>
            <div className="about-sub">AI-POWERED NETWORK CONFIGURATION</div>
            <div className="about-meta">
              <div>Agent v0.5.0 · UI v0.5.0</div>
              <div>Connected to <span className="mono">localhost:8000</span></div>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

Object.assign(window, { DashboardScreen, DevicesScreen, SettingsScreen });
