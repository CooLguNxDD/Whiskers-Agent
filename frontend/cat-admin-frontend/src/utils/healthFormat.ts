/**
 * Pure formatters for system-health and session-age display strings.
 */

/** Format tool-call success rate for the Live strip (e.g. "99.97%"). */
export function formatSuccessRate(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—"
  if (n >= 100) return "100%"
  if (n <= 0) return "0%"
  // Keep up to 2 decimals, strip trailing zeros
  const fixed = n.toFixed(2).replace(/\.?0+$/, "")
  return `${fixed}%`
}

/** Format p50 latency for the Live strip (e.g. "47ms"). */
export function formatP50(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms) || ms < 0) return "—"
  return `${Math.round(ms)}ms`
}

/** Compact session age from JWT iat (unix seconds). */
export function formatSessionAge(iatSec: number | null | undefined, nowMs: number = Date.now()): string {
  if (iatSec == null || !Number.isFinite(iatSec) || iatSec <= 0) return "—"
  const ageSec = Math.max(0, Math.floor(nowMs / 1000 - iatSec))
  if (ageSec < 60) return `${ageSec}s`
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m`
  if (ageSec < 86400) return `${Math.floor(ageSec / 3600)}h`
  return `${Math.floor(ageSec / 86400)}d`
}

/**
 * Build identity like "admin@local" from subject + instance name.
 * Uses "local" for localhost-like instance names.
 */
export function formatIdentity(
  subject: string | null | undefined,
  instanceName: string | null | undefined,
): string {
  const sub = (subject || "user").trim() || "user"
  const raw = (instanceName || "local").trim() || "local"
  const host = shortHost(raw)
  return `${sub}@${host}`
}

/** First DNS label of instance name; map localhost-ish hosts to "local". */
export function shortHost(instanceName: string): string {
  const first = instanceName.split(".")[0]?.trim() || instanceName
  const lower = first.toLowerCase()
  if (
    lower === "localhost" ||
    lower === "local" ||
    lower === "127" ||
    lower.startsWith("127-") ||
    lower === "0"
  ) {
    return "local"
  }
  return first
}

/** Human label for health status pill. */
export function formatHealthStatus(status: string | null | undefined): string {
  if (!status) return "unknown"
  return status.toLowerCase()
}
