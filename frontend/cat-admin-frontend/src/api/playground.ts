/**
 * Playground API — bridges the playground UI to the dynamic graph orchestrator.
 *
 * REST ops via OperationCatalog (owner `api.playground`). SSE streams resolve
 * their path from the catalog then use raw fetch (streaming body).
 */

import { tryRefreshSession } from "./client"
import { resolveCatalogPath } from "./catalogRuntime"
import { catalogClient } from "./catalogClient"

export interface ClarifyOption {
  label: string
  description?: string
  value: string
}

export interface ClarifyQuestion {
  header: string
  question: string
  multiSelect: boolean
  options: ClarifyOption[]
  value?: string
}

export interface GraphResponse {
  status?: string
  confirmation_needed?: boolean
  gate_decision?: string
  confidence?: number
  steps_executed?: number
  data?: unknown
  response?: unknown
  summary?: string
  message?: string
  clarification_question?: string
  questions?: ClarifyQuestion[]
  [k: string]: unknown
}

/**
 * Sends a message to the dynamic graph execution endpoint.
 */
export function runPlaygroundChat(
  message: string,
  forceExecute = false,
): Promise<GraphResponse> {
  return catalogClient.playground.chat(message, forceExecute)
}

export type GoapStreamEvent =
  | { type: "values"; state: Record<string, unknown> }
  | { type: "token"; node: string | null; text: string }
  | { type: "keepalive"; awaiting: "execution" | "elicitation" }
  | { type: "end" }
  | { type: "error"; message: string }

/**
 * Streams a POST request to an SSE endpoint and parses `data: {...}` chunks.
 * Retries once through a session refresh on 401 (mirrors api/client.ts semantics).
 */
async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (e: GoapStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  }
  const init: RequestInit = {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify(body),
    signal,
  }

  let response = await fetch(path, init)
  if (response.status === 401 && (await tryRefreshSession())) {
    response = await fetch(path, init)
  }

  if (!response.ok || !response.body) {
    throw new Error(`Stream failed: ${response.statusText}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder("utf-8")
  let buffer = ""

  if (signal) {
    signal.addEventListener("abort", () => {
      reader.cancel().catch(() => {})
    })
  }

  try {
    while (true) {
      if (signal?.aborted) break

      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      if (buffer.length > 1024 * 1024) {
        throw new Error("SSE stream buffer limit exceeded")
      }

      const parts = buffer.split("\n\n")
      buffer = parts.pop() || ""

      for (const part of parts) {
        if (part.startsWith("data: ")) {
          const jsonStr = part.slice(6)
          try {
            const chunk = JSON.parse(jsonStr) as GoapStreamEvent
            onEvent(chunk)
          } catch (e) {
            console.error("Failed to parse SSE chunk:", e, part)
            onEvent({ type: "error", message: "Malformed SSE chunk from server" })
          }
        }
      }
    }

    // The stream can end without a trailing blank-line delimiter (abrupt
    // abort, no final `done` frame) — drain whatever landed in `buffer`
    // instead of silently discarding it.
    if (buffer.startsWith("data: ")) {
      const jsonStr = buffer.slice(6)
      try {
        onEvent(JSON.parse(jsonStr) as GoapStreamEvent)
      } catch (e) {
        console.error("Failed to parse trailing SSE chunk:", e, buffer)
        onEvent({ type: "error", message: "Stream ended mid-chunk" })
      }
    }
  } finally {
    reader.cancel().catch(() => {})
  }
}

/**
 * Streams a message to the dynamic graph execution endpoint and parses SSE chunks.
 */
export async function streamPlaygroundGoap(
  message: string,
  forceExecute: boolean,
  onEvent: (e: GoapStreamEvent) => void,
  sessionId?: string | null,
  signal?: AbortSignal,
): Promise<void> {
  const resolved = await resolveCatalogPath(
    "api.playground",
    "api_playground_stream_goap",
  )
  const path = resolved?.path ?? "/api/playground/session_gated/stream_goap"
  return streamSSE(
    path,
    {
      message,
      force_execute: forceExecute,
      ...(sessionId ? { session_id: sessionId } : {}),
    },
    onEvent,
    signal,
  )
}

export interface ChatTurn {
  role: "user" | "assistant" | "system"
  content: string
}

/**
 * Streams a plain (graph-less) chat completion from the server's active LLM.
 */
export async function streamPlaygroundLLM(
  messages: ChatTurn[],
  onEvent: (e: GoapStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resolved = await resolveCatalogPath(
    "api.playground",
    "api_playground_chat_llm",
  )
  const path = resolved?.path ?? "/api/playground/session_gated/chat_llm"
  return streamSSE(path, { messages }, onEvent, signal)
}

export interface PlaygroundTool {
  name: string
  plugin: string
  description: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  enabled: boolean
}

/** Fetches every exposed MCP tool with its full input/output JSON schema. */
export function fetchPlaygroundTools(): Promise<{ tools: PlaygroundTool[] }> {
  return catalogClient.playground.tools()
}

export interface InvokeResult {
  ok: boolean
  tool: string
  ms: number
  structured_content: unknown
  content: unknown[]
  error: string | null
  error_type: string | null
}

/** Invokes a single MCP tool with arbitrary arguments; errors come back as data. */
export function invokePlaygroundTool(
  tool: string,
  args: Record<string, unknown>,
): Promise<InvokeResult> {
  return catalogClient.playground.invokeTool(tool, args)
}

export interface DBChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  engine?: string | null
  goap_state?: Record<string, unknown> | null
  raw?: GraphResponse | null
  created_at: string
}

export interface DBChatSession {
  id: string
  title: string
  created_at: string
  updated_at: string
  messages?: DBChatMessage[]
}

/** Creates a new chat session. */
export function createChatSession(title: string): Promise<DBChatSession> {
  return catalogClient.playground.createChatSession(title)
}

/** Lists all chat sessions, newest first. */
export function listChatSessions(): Promise<{ sessions: DBChatSession[] }> {
  return catalogClient.playground.listChatSessions()
}

/** Retrieves a single chat session with its messages. */
export function getChatSession(sessionId: string): Promise<DBChatSession> {
  return catalogClient.playground.getChatSession(sessionId)
}

/** Appends a message to a session. */
export function appendChatMessage(
  sessionId: string,
  role: "user" | "assistant",
  content: string,
  engine?: string | null,
  goapState?: Record<string, unknown> | null,
  raw?: GraphResponse | null,
): Promise<{ id: string }> {
  return catalogClient.playground.appendChatMessage(
    sessionId,
    role,
    content,
    engine,
    goapState,
    raw,
  )
}

export interface BackendGraphNode {
  id: string
  name: string
  data_type: string | null
}

export interface BackendGraphEdge {
  source: string
  target: string
  conditional: boolean
}

export interface BackendGraph {
  available: boolean
  nodes: BackendGraphNode[]
  edges: BackendGraphEdge[]
  error?: string
}

/** Fetches the backend LangGraph node/edge topology. */
export function fetchPlaygroundGraph(): Promise<BackendGraph> {
  return catalogClient.playground.graph()
}
