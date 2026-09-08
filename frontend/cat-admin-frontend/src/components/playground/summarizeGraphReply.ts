import type { GraphResponse } from "@/api/playground"

/** Envelope keys that mark a string as dumped graph/tool JSON, not chat prose. */
const ENVELOPE_KEYS = new Set([
  "status",
  "steps_executed",
  "data",
  "_meta",
  "selected_route",
  "step_results",
  "gate_decision",
])

/**
 * True when a string looks like a dumped run_graph / tool envelope rather than
 * user-facing natural language. Used so chat bubbles never echo raw JSON.
 */
export function looksLikeEnvelopeBlob(s: string): boolean {
  const t = s.trim()
  if (!t) return false
  const startsJson = t.startsWith("{") || t.startsWith("[")
  if (!startsJson) return false

  // Huge JSON-ish dumps are never chat prose (even if parse fails / truncated).
  if (t.length > 400) return true

  try {
    const parsed: unknown = JSON.parse(t)
    if (Array.isArray(parsed)) {
      // List of step/tool envelopes
      return (
        parsed.length > 0 &&
        typeof parsed[0] === "object" &&
        parsed[0] !== null &&
        "status" in (parsed[0] as object)
      )
    }
    if (parsed && typeof parsed === "object") {
      const keys = Object.keys(parsed as object)
      const hasStatus = keys.includes("status")
      const hasEnvelopeSibling = keys.some((k) => ENVELOPE_KEYS.has(k) && k !== "status")
      return hasStatus && (hasEnvelopeSibling || keys.includes("message") || keys.includes("error"))
    }
  } catch {
    // Truncated JSON still starts with { / [ — treat as blob once moderately long.
    return t.length > 80
  }
  return false
}

function pickText(...values: unknown[]): string | undefined {
  for (const v of values) {
    if (typeof v === "string" && v.trim()) {
      const trimmed = v.trim()
      if (looksLikeEnvelopeBlob(trimmed)) continue
      return trimmed
    }
  }
  return undefined
}

function nestedResponse(raw: GraphResponse): Record<string, unknown> | undefined {
  const nested = raw.response
  return nested && typeof nested === "object" && !Array.isArray(nested)
    ? (nested as Record<string, unknown>)
    : undefined
}

/** Best-effort one-liner from shaped tool `_meta` when no NL summary exists. */
function shapedMetaSummary(raw: GraphResponse): string | undefined {
  const data = raw.data
  const candidates: unknown[] = Array.isArray(data) ? data : data != null ? [data] : []
  for (const entry of candidates) {
    if (!entry || typeof entry !== "object") continue
    const e = entry as Record<string, unknown>
    const nested = e.data && typeof e.data === "object" ? (e.data as Record<string, unknown>) : e
    const meta = nested._meta
    if (!meta || typeof meta !== "object") continue
    const m = meta as Record<string, unknown>
    const tool = typeof m.tool === "string" ? m.tool : null
    const count = typeof m.item_count === "number" ? m.item_count : null
    if (tool && count != null) return `Completed ${tool} — ${count} item(s).`
    if (tool) return `Completed ${tool}.`
    if (count != null) return `Completed — ${count} item(s).`
  }
  return undefined
}

/**
 * User-facing assistant reply — prefers summary_node NL text over raw envelopes.
 */
export function summarizeGraphReply(
  raw: GraphResponse,
  goapState?: Record<string, unknown> | null
): string {
  const nested = nestedResponse(raw)
  const stateResp =
    goapState?.response && typeof goapState.response === "object" && !Array.isArray(goapState.response)
      ? (goapState.response as Record<string, unknown>)
      : undefined

  const text = pickText(
    raw.summary,
    raw.message,
    goapState?.summary,
    nested?.summary,
    nested?.message,
    stateResp?.summary,
    stateResp?.message,
    raw.clarification_question,
  )
  if (text) return text

  if (raw.status === "chat") {
    return pickText(nested?.message, raw.message) ?? "Completed"
  }
  if (raw.status === "halted") return "Halted by operator"
  if (raw.status === "need_input" || raw.clarification_question) {
    const q = raw.clarification_question
    if (typeof q === "string" && q.trim() && !looksLikeEnvelopeBlob(q)) return q.trim()
    return String(raw.status ?? "Need input")
  }
  if (raw.status === "confirmation_needed") {
    return pickText(raw.message) ?? "Confirmation needed"
  }
  if (raw.status === "error") {
    return pickText(raw.message, nested?.message) ?? "Error"
  }
  if (raw.status === "ok") {
    return (
      pickText(goapState?.summary as string) ??
      shapedMetaSummary(raw) ??
      `Done — ${raw.steps_executed ?? 0} step(s) executed`
    )
  }
  return String(raw.status ?? "Completed")
}
