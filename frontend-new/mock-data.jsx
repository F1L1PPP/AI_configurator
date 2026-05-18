// Scripted data + state for the prototype.

const MOCK_DEVICES = [
  {
    id: "router-01",
    name: "Router-01",
    ip: "192.168.1.1",
    model: "Cisco ISR 4321",
    ios: "IOS XE 17.6.1",
    status: "connected",
    health: "good",
    uptime: "23d 14h",
    lastSeen: "now",
  },
  {
    id: "router-02",
    name: "Router-02",
    ip: "192.168.1.2",
    model: "Cisco ISR 4331",
    ios: "IOS XE 17.6.1",
    status: "connected",
    health: "good",
    uptime: "12d 03h",
    lastSeen: "now",
  },
  {
    id: "router-03",
    name: "Router-03",
    ip: "192.168.1.3",
    model: "Cisco Catalyst 8300",
    ios: "IOS XE 17.9.2",
    status: "idle",
    health: "warning",
    uptime: "2d 18h",
    lastSeen: "4m ago",
  },
];

const RECENT_ACTIVITY = [
  { id: "act_20260518_8e18fd", text: "VLAN 30 OFFICE created on Router-01", time: "10:24 AM", kind: "applied" },
  { id: "act_20260518_4a91c2", text: "Interface GigabitEthernet0/1 IP updated", time: "10:21 AM", kind: "applied" },
  { id: "act_20260518_2db77a", text: "Backup created for Router-01", time: "10:18 AM", kind: "backup" },
  { id: "act_20260518_19fe04", text: "Session SES-0042 started", time: "10:15 AM", kind: "session" },
  { id: "act_20260518_0a3c11", text: "Hostname change rejected by operator", time: "10:09 AM", kind: "rejected" },
];

// Scripted chat: keyword -> assistant turn.
// Each turn produces a message (markdown) and optionally an "action proposal"
// that surfaces the sticky APPROVE / EXECUTE bar.
const CHAT_SCRIPTS = [
  {
    triggers: ["vlan", "office", "pridaj vlan"],
    delay: 1800,
    reply: {
      kind: "proposal",
      summary: "Add VLAN 30 named OFFICE on Router-01",
      actionId: "act_20260518_8e18fd",
      risk: "low",
      transport: "cli",
      commands: [
        "configure terminal",
        "vlan 30",
        " name OFFICE",
        " exit",
        "end",
        "write memory",
      ],
      verify: "show vlan brief | include OFFICE",
      affects: "Router-01 (192.168.1.1) · interface allocation: GigabitEthernet0/1",
      note: "Adds a new L2 VLAN. Existing traffic is not affected until an interface is moved into this VLAN.",
    },
  },
  {
    triggers: ["hostname", "rename", "zmen hostname"],
    delay: 1500,
    reply: {
      kind: "proposal",
      summary: "Change hostname to LAB-R1 on Router-01",
      actionId: "act_20260518_4a91c2",
      risk: "low",
      transport: "cli",
      commands: ["configure terminal", "hostname LAB-R1", "end", "write memory"],
      verify: "show running-config | include hostname",
      affects: "Router-01 (192.168.1.1)",
      note: "Hostname is cosmetic. SSH session prompt will change after apply.",
    },
  },
  {
    triggers: ["ospf", "routing", "process 100"],
    delay: 2400,
    reply: {
      kind: "proposal",
      summary: "Enable OSPF process 100, area 0 on Vlan1",
      actionId: "act_20260518_5fa1bb",
      risk: "medium",
      transport: "cli",
      commands: [
        "configure terminal",
        "router ospf 100",
        " network 192.168.1.0 0.0.0.255 area 0",
        " passive-interface default",
        " no passive-interface Vlan1",
        "end",
        "write memory",
      ],
      verify: "show ip ospf interface brief",
      affects: "Router-01 · L3 control plane",
      note: "OSPF will begin adjacency formation. Verify neighbors after apply.",
    },
  },
  {
    triggers: ["interface", "ip address", "gigabit"],
    delay: 1700,
    reply: {
      kind: "proposal",
      summary: "Set GigabitEthernet0/1 to 192.168.10.1/24",
      actionId: "act_20260518_91dd02",
      risk: "medium",
      transport: "cli",
      commands: [
        "configure terminal",
        "interface GigabitEthernet0/1",
        " ip address 192.168.10.1 255.255.255.0",
        " no shutdown",
        "end",
        "write memory",
      ],
      verify: "show ip interface brief | include GigabitEthernet0/1",
      affects: "Router-01 · GigabitEthernet0/1",
      note: "Brings the interface up with a new IP. Any prior address on this interface is replaced.",
    },
  },
  {
    triggers: ["static route", "10.99", "webui"],
    delay: 2200,
    reply: {
      kind: "proposal",
      summary: "Add static route 10.99.99.0/24 via 192.168.10.254 (WebUI)",
      actionId: "act_20260518_77abc1",
      risk: "low",
      transport: "webui",
      commands: [
        "Open WebUI → Configuration → Static Routing",
        "Click 'Add'",
        "Prefix: 10.99.99.0",
        "Prefix Mask: 255.255.255.0",
        "Next Hop: 192.168.10.254",
        "Click 'Apply'",
      ],
      verify: "Confirm row appears in Static Routes list",
      affects: "Router-01 · routing table",
      note: "A Chromium window will open during execute so you can watch the agent.",
    },
  },
  {
    triggers: ["trunk", "how", "ako"],
    delay: 1200,
    reply: {
      kind: "answer",
      text:
        "**Trunk port configuration** (IOS XE)\n\nA trunk port carries traffic for multiple VLANs between switches. The typical configuration is:\n\n```\ninterface GigabitEthernet0/1\n switchport mode trunk\n switchport trunk allowed vlan 10,20,30\n switchport trunk native vlan 99\n```\n\nMake sure the allowed VLAN list matches on both ends, and the native VLAN should not be a user VLAN.\n\n— Sources: Cisco IOS XE 17.x Configuration Guide, Layer 2 chapter.",
    },
  },
];

function matchScript(text) {
  const t = text.toLowerCase();
  for (const s of CHAT_SCRIPTS) {
    if (s.triggers.some((k) => t.includes(k))) return s;
  }
  return null;
}

const INITIAL_CHAT = [
  {
    role: "assistant",
    kind: "answer",
    text:
      "Hi. I'm the Cisco AI Config agent. Tell me what you want changed on a device — for example *add VLAN 30 named OFFICE*, *change hostname to LAB-R1*, or ask a question like *how do I configure a trunk port?*\n\nEvery write requires two clicks: **APPROVE**, then **EXECUTE**.",
  },
];

// Stream events that play out during execution
function buildExecuteStream(proposal) {
  if (proposal.transport === "cli") {
    return [
      { t: 0, line: "▶ EXECUTE → " + proposal.actionId, kind: "exec" },
      { t: 400, line: "  ssh router-01 (192.168.1.1)", kind: "info" },
      { t: 900, line: "  pre-snapshot saved → artifacts/.../pre.txt", kind: "info" },
      ...proposal.commands.map((cmd, i) => ({
        t: 1400 + i * 280,
        line: "  > " + cmd,
        kind: "cmd",
      })),
      { t: 1400 + proposal.commands.length * 280 + 300, line: "  verify → " + proposal.verify, kind: "verify" },
      { t: 1400 + proposal.commands.length * 280 + 800, line: "  post-snapshot saved", kind: "info" },
      { t: 1400 + proposal.commands.length * 280 + 1100, line: "✓ EXECUTED — action complete", kind: "ok" },
    ];
  }
  return [
    { t: 0, line: "▶ EXECUTE → " + proposal.actionId, kind: "exec" },
    { t: 400, line: "  launching Chromium…", kind: "info" },
    { t: 1000, line: "  open https://192.168.1.1", kind: "step" },
    ...proposal.commands.map((cmd, i) => ({
      t: 1500 + i * 700,
      line: "  step " + (i + 1) + ": " + cmd,
      kind: "step",
    })),
    { t: 1500 + proposal.commands.length * 700 + 400, line: "  screenshot saved", kind: "info" },
    { t: 1500 + proposal.commands.length * 700 + 900, line: "✓ EXECUTED — action complete", kind: "ok" },
  ];
}

const HEALTH_SUMMARY = {
  status: "good",
  metrics: [
    { k: "Devices connected", v: "2 / 3" },
    { k: "Active sessions", v: "3" },
    { k: "Configs saved", v: "12" },
    { k: "Last backup", v: "06m ago" },
  ],
};

Object.assign(window, {
  MOCK_DEVICES,
  RECENT_ACTIVITY,
  CHAT_SCRIPTS,
  INITIAL_CHAT,
  HEALTH_SUMMARY,
  matchScript,
  buildExecuteStream,
});
