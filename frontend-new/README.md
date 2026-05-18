# Cisco AI Config — Frontend Prototype

A static, click-through prototype for the AI-driven Cisco router configurator UI. Pure HTML + React (via Babel in the browser) + CSS. **No backend — every interaction is scripted with mock data.** Use it as the visual + interaction reference when you wire the real frontend up.

## How to view it

The HTML files load JSX files at runtime via `fetch`, so opening `index.html` straight from disk (`file://`) won't work — browsers block local file fetches. You need a tiny local server.

Pick any one:

```bash
# Option A — Python (already on most machines)
cd path/to/this/folder
python3 -m http.server 8080
# then open http://localhost:8080

# Option B — Node
cd path/to/this/folder
npx serve .

# Option C — VS Code "Live Server" extension
# Right-click index.html → "Open with Live Server"
```

## File layout

```
index.html              ← entry point, loads everything
styles.css              ← all styling (theme tokens at the top)
app.jsx                 ← top-level <App>, routing, theme integration
chrome.jsx              ← AppShell, Sidebar, TopBar, Card, Btn, icons
mesh.jsx                ← visual primitives: MeshSphere, EthernetCableLogo,
                          InteractiveMeshWave, AnimatedGlobe (cable meteors),
                          MeshScatter
mock-data.jsx           ← MOCK_DEVICES, RECENT_ACTIVITY, CHAT_SCRIPTS,
                          INITIAL_CHAT, matchScript(), buildExecuteStream()
screens-basic.jsx       ← DashboardScreen, DevicesScreen, SettingsScreen
screen-ai.jsx           ← ChatScreen — chat + live event stream + sticky
                          APPROVE / EXECUTE bar
screen-preview.jsx      ← PreviewScreen — CLI diff + change summary + apply
tweaks-panel.jsx        ← runtime theme/accent/mesh tweak controls
assets/                 ← cable-logo.png + white variant, router-mesh.png +
                          white variant, mesh-pattern.svg
```

## Routes (sidebar)

| ID         | Screen                  | File                |
| ---------- | ----------------------- | ------------------- |
| `dashboard`| Dashboard overview      | `screens-basic.jsx` |
| `devices`  | Connect + list devices  | `screens-basic.jsx` |
| `ai`       | AI Configuration (chat) | `screen-ai.jsx`     |
| `preview`  | Config Preview (diff)   | `screen-preview.jsx`|
| `settings` | Settings                | `screens-basic.jsx` |

The active route lives in `App` state in `app.jsx`. From AI Configuration, clicking **View diff** on the sticky approve bar jumps to Preview with that proposal.

## Removing the mock data (when integrating)

Everything that's pre-recorded lives in **`mock-data.jsx`**. Replace each constant or helper with a real call:

| Mock                    | Used by              | Replace with                                              |
| ----------------------- | -------------------- | --------------------------------------------------------- |
| `MOCK_DEVICES`          | Devices, Dashboard   | `GET /api/devices` response                               |
| `RECENT_ACTIVITY`       | Dashboard            | `GET /api/actions/recent` response                        |
| `INITIAL_CHAT`          | AI Configuration     | First message can be empty `[]` — real backend won't seed |
| `matchScript(text)`     | AI Configuration     | `POST /api/chat { message }` — backend returns the reply |
| `buildExecuteStream()`  | AI Configuration     | Subscribe to `ws://.../ws/agent` and append every event   |
| `CHAT_SCRIPTS` array    | the matcher above    | Delete — backend produces proposals dynamically           |

### Specifically in `screen-ai.jsx`:

The `send(text)` function fakes the whole flow with `setTimeout` calls. Replace it with something like:

```jsx
const send = async (text) => {
  setMessages(m => [...m, { role: "user", text }]);
  setTyping(true);
  setPhase("thinking");
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
  });
  const reply = await res.json();
  setTyping(false);
  if (reply.kind === "answer") {
    setMessages(m => [...m, { role: "assistant", kind: "answer", text: reply.text }]);
    setPhase("idle");
  } else {
    setMessages(m => [...m, { role: "assistant", kind: "proposal", proposal: reply }]);
    setPending(reply);
    setPhase("awaiting");
  }
};
```

Replace `onApprove` / `onReject` / `onExecute` with `POST /api/approve/{id}`, `/api/reject/{id}`, `/api/execute/{id}` calls.

Replace the stream simulation (`buildExecuteStream`) with a real WebSocket — wire `ws://localhost:8000/ws/agent` events into `setStream`:

```jsx
useEffect(() => {
  const ws = new WebSocket("ws://localhost:8000/ws/agent");
  ws.onmessage = e => {
    const evt = JSON.parse(e.data);
    setStream(s => [...s, { line: evt.text, kind: evt.kind }]);
  };
  return () => ws.close();
}, []);
```

### In `screens-basic.jsx`:

- **DevicesScreen** — replace the `onConnect` fake `setTimeout` with `POST /api/devices` and re-fetch the list. Replace `MOCK_DEVICES` import with a state populated from `GET /api/devices`.
- **DashboardScreen** — replace `RECENT_ACTIVITY` with a fetch in a `useEffect`.

### In `screen-preview.jsx`:

The fake `before` / `after` arrays in `PreviewScreen` should come from `GET /api/preview/{actionId}` — the backend returns the running-config diff. The proposal data already arrives via the `preview` prop from `App`.

## Theme & accent

Settings → Appearance has a Light/Dark switch and 5 accent colors. Same controls are in the Tweaks panel (toolbar toggle). State persists in the source HTML between reloads via the `/*EDITMODE-BEGIN*/…/*EDITMODE-END*/` block in `app.jsx` — feel free to delete that mechanism if you don't want self-editing defaults.

## Notes

- All animations (rotating mesh sphere, cable meteors hooking the globe, interactive wave, logo sway) are CSS + canvas — no animation libraries.
- The mesh wave at the bottom of the dashboard listens for `mousemove` + `click` on the canvas. Cursor pulls the wave; click drops an expanding ripple.
- Cable meteors fire ~every 4s. The hook animation: fly-in → impact → pendulum swing → settle → gravity drop.
- All decoration assets (`cable-logo.png`, `router-mesh.png`) have black + white variants so they invert in dark mode automatically via CSS.
