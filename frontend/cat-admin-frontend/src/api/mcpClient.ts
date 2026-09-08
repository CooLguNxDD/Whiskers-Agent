/**
 * MCP client — drives the backend FastMCP server's `run_graph` tool directly
 * over a persistent Streamable HTTP transport (the "MCP Client-Host" model).
 *
 * Path 1 alternative to the REST/SSE playground (`playground.ts`): instead of
 * custom endpoints, the browser is a real MCP Host. Live GOAP state arrives as
 * progress notifications (the backend JSON-encodes the SSE-style envelope into
 * each notification's `message`), and confirmation/clarification is handled via
 * MCP elicitation — resolved by the UI within the SAME tool call.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js"
import { ElicitRequestSchema, type ElicitResult } from "@modelcontextprotocol/sdk/types.js"
import type { GoapStreamEvent, GraphResponse } from "./playground"
import { api } from "./client"

/**
 * Resolves an MCP elicitation request from the UI. Receives the server's prompt
 * and the requested JSON schema; returns an `ElicitResult` (accept/decline/cancel).
 */
export type ElicitationResolver = (
  message: string,
  requestedSchema: Record<string, unknown>,
) => Promise<ElicitResult>

/** Per-idle-window timeout; reset by server keepalives during long LLM/model loads. */
const MCP_TOOL_TIMEOUT_MS = 600_000

let _client: Client | null = null
let _clientUrl: string | null = null
let _connecting: Promise<Client> | null = null
let _elicitResolver: ElicitationResolver | null = null

/** Register the UI handler that answers elicitation prompts. Pass null to clear. */
export function setElicitationResolver(fn: ElicitationResolver | null): void {
  _elicitResolver = fn
}

/**
 * Exchange the admin session cookie for a short-lived MCP Bearer.
 * `resource` is the absolute `/mcp` URL the token is valid for (empty token when
 * OAuth is off). Used to decide whether the token may be attached to a target.
 */
export async function fetchMcpToken(): Promise<{ token: string; resource: string }> {
  const data = await api.get<{ access_token?: string; resource?: string }>("/api/playground/session_gated/mcp-token")
  return { token: data.access_token ?? "", resource: data.resource ?? "" }
}

/** True when `url` targets our own backend (the token's resource) or is same-origin. */
export function isOwnBackend(url: URL, pathOrUrl: string, resource: string): boolean {
  if (pathOrUrl.startsWith("/") && !pathOrUrl.startsWith("//")) return true // relative → proxied to our backend
  if (url.origin === window.location.origin) return true
  try {
    return !!resource && url.origin === new URL(resource).origin
  } catch {
    return false
  }
}

/** Open and initialize a persistent MCP session. */
async function connect(urlOverride?: string): Promise<Client> {
  const { token, resource } = await fetchMcpToken()

  // Resolve URL override or use default
  let url: URL
  const pathOrUrl = urlOverride || "/mcp"
  if (/^https?:\/\//.test(pathOrUrl)) {
    url = new URL(pathOrUrl)
  } else {
    url = new URL(pathOrUrl, window.location.origin)
  }

  // Attach the admin token only when talking to our own backend — never leak it
  // to an arbitrary external MCP server chosen via the server-URL override.
  const headers: Record<string, string> = {}
  if (token && isOwnBackend(url, pathOrUrl, resource)) {
    headers.Authorization = `Bearer ${token}`
  }

  const transport = new StreamableHTTPClientTransport(url, {
    requestInit: { headers, credentials: "include" },
  })

  const client = new Client(
    { name: "cat-admin-playground", version: "1.0.0" },
    { capabilities: { elicitation: {} } },
  )

  // Server pauses run_graph and asks for a decision → defer to the UI resolver.
  client.setRequestHandler(ElicitRequestSchema, async (req): Promise<ElicitResult> => {
    const params = req.params as { message: string; requestedSchema?: Record<string, unknown> }
    if (!_elicitResolver) return { action: "decline" }
    return _elicitResolver(params.message, params.requestedSchema ?? {})
  })

  await client.connect(transport)
  return client
}

/**
 * Lazily establish (and reuse) the singleton MCP session.
 * Supports URL override and tears down old session if target URL changes.
 */
export async function getMcpClient(urlOverride?: string): Promise<Client> {
  const targetUrl = urlOverride || "/mcp"
  if (_client && _clientUrl === targetUrl) return _client

  // Tear down old client synchronously if URL has changed to avoid yielding
  if (_client || _connecting) {
    const c = _client
    _client = null
    _clientUrl = null
    _connecting = null
    if (c) {
      void c.close().catch(() => {})
    }
  }

  const requestedUrl = targetUrl
  _clientUrl = targetUrl
  _connecting = connect(targetUrl)
    .then((c) => {
      if (_clientUrl !== requestedUrl) {
        void c.close().catch(() => {})
        return c
      }
      _client = c
      return c
    })
    .finally(() => {
      if (_clientUrl === requestedUrl) {
        _connecting = null
      }
    })
  return _connecting
}

/**
 * Tear down the session so the next call reconnects.
 * Resets the cached client instance and active URL.
 */
export async function resetMcpClient(): Promise<void> {
  const c = _client
  _client = null
  _clientUrl = null
  _connecting = null
  try {
    await c?.close()
  } catch {
    /* ignore close errors */
  }
}

export interface RunGraphCallbacks {
  /** Live GOAP state snapshot (decoded from a `values` notification). */
  onState?: (state: Record<string, unknown>) => void
  /** Streaming token from a graph node (decoded from a `token` notification). */
  onToken?: (node: string | null, text: string) => void
  /** Server keepalive during long silent phases (model load, decompose, elicitation). */
  onKeepalive?: (awaiting: "execution" | "elicitation") => void
}

/**
 * Call `run_graph` over MCP (attaching sessionId for cross-turn memory if provided),
 * relaying progress notifications to the callbacks and returning the tool's final structured envelope.
 */
export async function runGraphMcp(
  userMessage: string,
  cb: RunGraphCallbacks = {},
  urlOverride?: string,
  sessionId?: string | null,
): Promise<GraphResponse> {
  const client = await getMcpClient(urlOverride)
  const result = await client.callTool(
    {
      name: "run_graph",
      arguments: {
        user_message: userMessage,
        ...(sessionId ? { session_id: sessionId } : {}),
      },
    },
    undefined,
    {
      timeout: MCP_TOOL_TIMEOUT_MS,
      resetTimeoutOnProgress: true, // server keepalives (~15s) reset this during model load + elicitation
      onprogress: (p) => {
        if (!p.message) return
        let ev: GoapStreamEvent
        try {
          ev = JSON.parse(p.message) as GoapStreamEvent
        } catch {
          return // non-JSON progress message — ignore
        }
        if (ev.type === "values") cb.onState?.(ev.state)
        else if (ev.type === "token") cb.onToken?.(ev.node, ev.text)
        else if (ev.type === "keepalive") cb.onKeepalive?.(ev.awaiting)
      },
    },
  )

  // FastMCP returns the dict envelope as structuredContent; fall back to text.
  const structured = (result as { structuredContent?: unknown }).structuredContent
  if (structured && typeof structured === "object" && !Array.isArray(structured)) {
    return structured as GraphResponse
  }

  const content = (result as { content?: Array<{ type: string; text?: string }> }).content
  const text = content?.find((c) => c.type === "text")?.text
  if (text) {
    try {
      return JSON.parse(text) as GraphResponse
    } catch {
      // Never put raw / truncated JSON into message — chat would dump the envelope.
      const trimmed = text.trim()
      const looksJson = trimmed.startsWith("{") || trimmed.startsWith("[")
      if (looksJson) {
        return {
          status: "error",
          message: "Received an unreadable tool response (truncated or invalid JSON).",
        }
      }
      return { message: text }
    }
  }
  return {}
}
