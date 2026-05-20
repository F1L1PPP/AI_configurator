// window.api — fetch + WebSocket adapter for the FastAPI backend. Phase 1 of new-frontend migration.

(function () {
  var API_BASE = window.__API_BASE__ || "";

  // Derive the WebSocket base from API_BASE. Empty string = same-origin,
  // so we use location.origin to build the ws:// URL in that case.
  function wsBase() {
    if (API_BASE === "") {
      return (location.protocol === "https:" ? "wss://" : "ws://") + location.host;
    }
    return API_BASE.replace(/^http/, "ws");
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  // Build a URL with URLSearchParams so callers can't inject metacharacters.
  function url(path, params) {
    var base = API_BASE + path;
    if (params) {
      base += "?" + new URLSearchParams(params).toString();
    }
    return base;
  }

  // Read FastAPI's {"detail": "..."} body and throw an Error with that text.
  async function throwFromResponse(res) {
    var detail;
    try {
      var text = await res.text();
      try {
        var parsed = JSON.parse(text);
        detail = (typeof parsed.detail === "string") ? parsed.detail : text;
      } catch (_) {
        detail = text;
      }
    } catch (_) {
      detail = res.statusText;
    }
    throw new Error(detail || ("HTTP " + res.status));
  }

  // POST with no request body; throw on non-2xx.
  async function postEmpty(path) {
    var res = await fetch(API_BASE + path, { method: "POST" });
    if (!res.ok) await throwFromResponse(res);
    return res.json();
  }

  // ---------------------------------------------------------------------------
  // Shape adapters
  // ---------------------------------------------------------------------------

  // Map a raw log entry from /api/logs/recent to {id, text, time, kind}.
  function adaptLogEntry(entry) {
    var id = entry.action_id || ("log_" + entry.timestamp);

    var text = entry.result_summary || entry.event || entry.tool || "(no summary)";

    var time = "";
    if (entry.timestamp) {
      try {
        time = new Date(entry.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (_) {
        time = entry.timestamp;
      }
    }

    var kind = "info";
    if (entry.event) {
      if (/applied/.test(entry.event))  { kind = "applied"; }
      else if (/backup/.test(entry.event))   { kind = "backup"; }
      else if (/session/.test(entry.event))  { kind = "session"; }
      else if (/rejected/.test(entry.event)) { kind = "rejected"; }
    }

    return { id: id, text: text, time: time, kind: kind };
  }

  // Map /api/actions/{id} response to the shape screens need.
  // Returns graceful fallback when no real diff snapshot exists yet.
  function adaptPreview(action) {
    var before = action.snapshot_pre
      ? action.snapshot_pre.split("\n")
      : null;
    var after = action.snapshot_post
      ? action.snapshot_post.split("\n")
      : null;

    if (!before || !after) {
      return {
        action: action,
        before: [],
        after: [],
        addedSet: new Set(),
        commands: (action.params && action.params.commands) ? action.params.commands : [],
        note: "No diff snapshot available. Execute to materialize before/after.",
      };
    }

    // Build addedSet: lines present in after but not in before.
    var beforeSet = new Set(before);
    var addedSet = new Set();
    after.forEach(function (line) {
      if (!beforeSet.has(line)) addedSet.add(line);
    });

    return {
      action: action,
      before: before,
      after: after,
      addedSet: addedSet,
      commands: (action.params && action.params.commands) ? action.params.commands : [],
      note: null,
    };
  }

  // ---------------------------------------------------------------------------
  // WebSocket reconnect client (ported from frontend/lib/ws.ts)
  // ---------------------------------------------------------------------------
  // Backoff: 500ms → doubles each attempt → cap 10 000ms.
  // Max 20 consecutive failures before giving up (calls onStatus("error")).
  // Counter resets to 0 on every successful open.
  // closedByCaller flag prevents reconnect after the caller calls handle.close().

  function connectAgentWs(onEvent, onStatus) {
    var MAX_BACKOFF_MS = 10000;
    var MAX_RECONNECT_ATTEMPTS = 20;
    var ws = null;
    var closedByCaller = false;
    var backoffMs = 500;
    var consecutiveFailures = 0;
    var reconnectTimer = null;

    function open() {
      if (closedByCaller) return;
      ws = new WebSocket(wsBase() + "/ws/agent");

      ws.onopen = function () {
        backoffMs = 500;
        consecutiveFailures = 0;
        if (onStatus) onStatus("open");
      };

      ws.onclose = function () {
        if (onStatus) onStatus("closed");
        if (closedByCaller) return;
        consecutiveFailures += 1;
        if (consecutiveFailures >= MAX_RECONNECT_ATTEMPTS) {
          console.warn("[ws/agent] gave up after " + MAX_RECONNECT_ATTEMPTS + " reconnect attempts");
          if (onStatus) onStatus("error");
          return;
        }
        reconnectTimer = setTimeout(open, backoffMs);
        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
      };

      ws.onerror = function () {
        if (onStatus) onStatus("error");
      };

      ws.onmessage = function (msg) {
        try {
          var ev = JSON.parse(msg.data);
          onEvent(ev);
        } catch (err) {
          console.warn("[ws/agent] failed to parse frame:", err, msg.data);
        }
      };
    }

    open();

    return {
      close: function () {
        closedByCaller = true;
        if (reconnectTimer !== null) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        if (ws) ws.close();
      },
    };
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  Object.assign(window, {
    api: {

      // GET /api/suggestions?device_id=X → array of suggestion strings, [] on failure.
      // device_id defaults to "router-01" on the server; pass undefined to use that default.
      fetchSuggestions: async function (deviceId) {
        try {
          var qs = deviceId ? "?device_id=" + encodeURIComponent(deviceId) : "";
          var res = await fetch(url("/api/suggestions" + qs));
          if (!res.ok) {
            console.error("[api] fetchSuggestions HTTP " + res.status);
            return [];
          }
          var data = await res.json();
          return Array.isArray(data.suggestions) ? data.suggestions : [];
        } catch (err) {
          console.error("[api] fetchSuggestions network error:", err);
          return [];
        }
      },

      // GET /api/devices → parsed array, [] on failure.
      fetchDevices: async function () {
        try {
          var res = await fetch(url("/api/devices"));
          if (!res.ok) {
            console.error("[api] fetchDevices HTTP " + res.status);
            return [];
          }
          return res.json();
        } catch (err) {
          console.error("[api] fetchDevices network error:", err);
          return [];
        }
      },

      // GET /api/devices/{id}/last-backup → {action_id, taken_at, snapshot_path, count}.
      // Returns null fields + count 0 when no snapshots exist yet.
      fetchLastBackup: async function (deviceId) {
        try {
          var res = await fetch(url("/api/devices/" + encodeURIComponent(deviceId) + "/last-backup"));
          if (!res.ok) {
            console.error("[api] fetchLastBackup HTTP " + res.status);
            return { action_id: null, taken_at: null, snapshot_path: null, count: 0 };
          }
          return res.json();
        } catch (err) {
          console.error("[api] fetchLastBackup network error:", err);
          return { action_id: null, taken_at: null, snapshot_path: null, count: 0 };
        }
      },

      // GET /api/actions/{id}/snapshot/{phase} → running-config text, null on any failure.
      // phase must be "pre" or "post". 404/422/network all collapse to null — caller
      // renders the empty-state placeholder uniformly.
      fetchSnapshot: async function (actionId, phase) {
        try {
          var res = await fetch(url("/api/actions/" + encodeURIComponent(actionId) + "/snapshot/" + encodeURIComponent(phase)));
          if (!res.ok) {
            return null;
          }
          var data = await res.json();
          return typeof data.running_config === "string" ? data.running_config : null;
        } catch (err) {
          console.error("[api] fetchSnapshot network error:", err);
          return null;
        }
      },

      // GET /api/logs/recent?limit=N → [{id, text, time, kind}], [] on failure.
      fetchRecentActivity: async function (limit) {
        if (limit === undefined) limit = 10;
        try {
          var res = await fetch(url("/api/logs/recent", { limit: String(limit) }));
          if (!res.ok) {
            console.error("[api] fetchRecentActivity HTTP " + res.status);
            return [];
          }
          var body = await res.json();
          if (!Array.isArray(body)) return [];
          return body.map(adaptLogEntry);
        } catch (err) {
          console.error("[api] fetchRecentActivity network error:", err);
          return [];
        }
      },

      // GET /api/actions/{actionId} → {action, before, after, addedSet, commands, note}.
      // Also parallel-fetches pre/post snapshot content so adaptPreview can build a real diff.
      fetchPreview: async function (actionId) {
        try {
          var res = await fetch(url("/api/actions/" + encodeURIComponent(actionId)));
          if (!res.ok) return null;
          var action = await res.json();
          // Pull both snapshots in parallel. Returns null on any failure path; the
          // adaptPreview's existing null-guard renders the empty-state cleanly.
          var snapshots = await Promise.all([
            this.fetchSnapshot(actionId, "pre"),
            this.fetchSnapshot(actionId, "post"),
          ]);
          action.snapshot_pre = snapshots[0];
          action.snapshot_post = snapshots[1];
          return adaptPreview(action);
        } catch (err) {
          console.error("[api] fetchPreview network error:", err);
          return null;
        }
      },

      // POST /api/chat {message, history} → full ChatResponse; throws on non-2xx.
      sendChat: async function (message, history) {
        if (history === undefined) history = [];
        var res = await fetch(url("/api/chat"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: message, history: history }),
        });
        if (!res.ok) await throwFromResponse(res);
        return res.json();
      },

      // POST /api/approve/{id} (no body) → JSON; throws on non-2xx.
      approveAction: async function (id) {
        return postEmpty("/api/approve/" + encodeURIComponent(id));
      },

      // POST /api/reject/{id} (no body) → JSON; throws on non-2xx.
      rejectAction: async function (id) {
        return postEmpty("/api/reject/" + encodeURIComponent(id));
      },

      // POST /api/execute/{id} (no body) → JSON; throws on non-2xx.
      // Note: this call blocks 1-30s while the router executes.
      executeAction: async function (id) {
        return postEmpty("/api/execute/" + encodeURIComponent(id));
      },

      // Reconnecting WebSocket client — returns {close()}.
      // See connectAgentWs() above for backoff + max-attempts detail.
      connectAgentWs: connectAgentWs,
    },
  });
})();
