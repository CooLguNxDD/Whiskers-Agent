/**
 * Shared types for the Playground feature.
 * Re-exports API types from @/api/playground for component consumption.
 */

export type { GraphResponse, GoapStreamEvent } from "@/api/playground"

/** A saved MCP server connection profile. */
export interface PGServer {
  id: string
  label: string
  url: string
  transport: "sse" | "streamable" | "stdio"
  latency: string
  status: "ok" | "warn" | "off"
}

/** A single JSON Schema property definition. */
export interface JSONSchemaProperty {
  type?: string
  format?: string
  description?: string
  enum?: string[]
  pattern?: string
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  items?: JSONSchemaProperty
  properties?: Record<string, JSONSchemaProperty>
  required?: string[]
}

/** Full JSON Schema object for a tool's input or output. */
export interface JSONSchema extends JSONSchemaProperty {
  type: "object"
  properties: Record<string, JSONSchemaProperty>
}

/** A tool registered in the playground's tool registry. */
export interface PGTool {
  name: string
  plugin: string
  desc: string
  input: JSONSchema
  output: JSONSchema
}

/** A tool call record returned within a chat assistant message. */
export interface ToolCall {
  tool: string
  ms: number
  tokens: number
  status: "ok" | "warn" | "err"
  args: Record<string, unknown>
  result: Record<string, unknown>
}

/** A row in the group test results table. */
export interface GroupTestResult {
  tool: string
  status: "pass" | "fail" | "skip"
  ms: number
  tokens: number
  err: string | null
}

/** A chat message in the Chat mode thread. */
export interface ChatMessage {
  role: "user" | "assistant"
  text: string
  raw?: import("@/api/playground").GraphResponse
  canConfirm?: boolean
  canClarify?: boolean
  originalText?: string
  /** Which engine produced this assistant message. */
  engine?: "agent" | "llm"
  /** Merged graph-state snapshot captured from the agent stream (agent only). */
  goapState?: Record<string, unknown>
  /** Token/exec log accumulated during the agent stream (agent only). */
  log?: import("../goap/types").LogEntry[]
  /** True while the message is still receiving stream events. */
  streaming?: boolean
}
