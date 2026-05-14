// User-facing error catalog. Translates HTTP/network failures into
// messages a network engineer can act on without reading a Python
// traceback.
//
// Review §3 HIGH: the previous UI showed raw `Error: HTTP 503` strings,
// which tells the operator nothing. The catalog below gives a clear
// "what" + a clear "next step" for every status code the backend
// actually emits, plus a network-failure path.
//
// Backend status codes that hit this catalog (see backend/api/):
//   401 — SSH auth fail (Netmiko NetMikoAuthenticationException)
//   403 — never returned today (audit B1 fix moved everything to 409)
//   404 — action_id unknown
//   409 — wrong state for operation (TOCTOU + tightened approve)
//   422 — validator rejected propose-time / execute-time params
//   500 — unhandled exception / tool_failed
//   503 — router unreachable, Netmiko timeout, Anthropic 5xx
//
// If a future endpoint adds a new code, add it here too — the catalog
// is the single source of truth for what the operator sees.

export type FriendlyError = {
  /** One-line summary that becomes the headline of the error banner. */
  title: string;
  /** Concrete next step. Filip is a Cisco engineer, not a web debugger
   *  — tell him to check `.env`, ping the router, look at logs/, etc. */
  hint: string;
};

const STATUS_CATALOG: Record<number, FriendlyError> = {
  401: {
    title: "SSH authentication failed",
    hint: "Check ROUTER_SSH_USER and ROUTER_SSH_PASSWORD in .env, then restart the backend.",
  },
  404: {
    title: "Action not found",
    hint: "The action_id in the URL or chat reply may have expired (server restart wipes the in-memory store). Start a fresh action from /chat.",
  },
  409: {
    title: "Action is in the wrong state",
    hint: "The action may already be running, already executed, or already rejected. Refresh and start a fresh action from /chat.",
  },
  422: {
    title: "Invalid parameters",
    hint: "The router config you asked for failed validation. The server-side detail below explains which field is wrong — fix it and try again.",
  },
  500: {
    title: "Server error",
    hint: "Something inside the backend went wrong. Check the most recent entries in logs/ for the stack trace.",
  },
  503: {
    title: "Router unreachable",
    hint: "SSH to the router timed out. Check ROUTER_HOST in .env, then `ping <ip>` from this machine. Cable/VPN/firewall are the usual culprits.",
  },
};

const NETWORK_FAILURE: FriendlyError = {
  title: "Cannot reach the backend",
  hint: "The Python backend isn't responding. Make sure `uvicorn backend.main:app` is running, then refresh.",
};

const TIMEOUT: FriendlyError = {
  title: "Request timed out",
  hint: "The operation took longer than the client allowed. WebUI flows can run 20–30s, so this usually means Playwright stalled or the router stopped responding. Check the WebUI Live tab.",
};

const GENERIC: FriendlyError = {
  title: "Unexpected error",
  hint: "Capture the detail below and the most recent logs/ entries — that combo is what you'd attach to a bug report.",
};

/**
 * Translate an HTTP status + optional server detail into a friendly
 * `{ title, hint, detail }` block ready to render.
 *
 * `detail` is the raw server response body (often a useful 422 message
 * like "invalid hostname 'foo bar': must be 1-63 chars"). We keep it
 * verbatim because the backend already crafts these to be readable.
 */
export function describeHttpError(
  status: number,
  detail?: string,
): FriendlyError & { detail?: string } {
  const base = STATUS_CATALOG[status] ?? {
    title: `HTTP ${status}`,
    hint: GENERIC.hint,
  };
  return { ...base, detail };
}

/**
 * Translate a thrown JS error (fetch network failure, AbortError, etc.)
 * into a friendly block. Distinguishes timeout/abort from "backend is
 * down" because the operator's next step is different for each.
 */
export function describeNetworkError(err: unknown): FriendlyError & { detail?: string } {
  if (err instanceof DOMException && err.name === "AbortError") {
    return { ...TIMEOUT, detail: err.message };
  }
  if (err instanceof TypeError) {
    // fetch throws TypeError on network-layer failures (DNS, connection
    // refused, CORS). Most common cause is "backend not running".
    return { ...NETWORK_FAILURE, detail: err.message };
  }
  return {
    ...GENERIC,
    detail: err instanceof Error ? err.message : String(err),
  };
}

/**
 * Convenience for callers that have a Response object — reads the
 * response body for the detail and returns a friendly block. Use this
 * in the catch branch of any fetch() call.
 */
export async function fromResponse(res: Response): Promise<FriendlyError & { detail?: string }> {
  let detail: string | undefined;
  try {
    const text = await res.text();
    // FastAPI returns `{"detail": "..."}` for HTTPExceptions; surface
    // the inner detail if present, otherwise the raw body.
    try {
      const parsed = JSON.parse(text);
      detail = typeof parsed?.detail === "string" ? parsed.detail : text;
    } catch {
      detail = text;
    }
  } catch {
    detail = res.statusText;
  }
  return describeHttpError(res.status, detail || undefined);
}
