/**
 * Pure live-metrics overlay — merge WS snapshot onto the range summary
 * for KPI cards and the error stream without putting server data in Zustand.
 */

import type { AnalyticsSummaryResponse, RecentError } from "@/api/analytics"
import type { LiveMetrics } from "@/hooks/useAnalytics"

export type OverlayKpis = {
  activeSessions: number
  totalCalls: number
  successRate: number
  p50: number
  p99: number
  graphCalls: number
  graphSuccessRate: number
}

/** Format a unix-seconds error timestamp as a relative "when" string. */
export function formatErrorWhen(timestampSec: number, nowMs = Date.now()): string {
  const timeDiffSec = Math.max(0, Math.floor(nowMs / 1000 - timestampSec))
  if (timeDiffSec >= 3600) return `${Math.floor(timeDiffSec / 3600)}h ago`
  if (timeDiffSec >= 60) return `${Math.floor(timeDiffSec / 60)}m ago`
  return "just now"
}

/** Overlay live KPI fields onto the historical summary (live wins when present). */
export function overlayKpis(
  arg1: AnalyticsSummaryResponse | LiveMetrics | null | undefined,
  arg2: LiveMetrics | AnalyticsSummaryResponse | null | undefined,
): OverlayKpis {
  let summary: AnalyticsSummaryResponse | undefined
  let liveMetrics: LiveMetrics | null = null

  if (arg1 && typeof arg1 === "object" && "kpi" in arg1) {
    summary = arg1 as unknown as AnalyticsSummaryResponse
    liveMetrics = (arg2 as unknown as LiveMetrics) ?? null
  } else if (arg2 && typeof arg2 === "object" && "kpi" in arg2) {
    summary = arg2 as unknown as AnalyticsSummaryResponse
    liveMetrics = (arg1 as unknown as LiveMetrics) ?? null
  } else {
    if (arg1 && typeof arg1 === "object" && ("summary" in arg1 || "relay" in arg1)) {
      liveMetrics = arg1 as unknown as LiveMetrics
      summary = (arg2 as unknown as AnalyticsSummaryResponse) ?? undefined
    } else if (arg2 && typeof arg2 === "object" && ("summary" in arg2 || "relay" in arg2)) {
      liveMetrics = arg2 as unknown as LiveMetrics
      summary = (arg1 as unknown as AnalyticsSummaryResponse) ?? undefined
    } else {
      summary = (arg1 as unknown as AnalyticsSummaryResponse) ?? undefined
      liveMetrics = (arg2 as unknown as LiveMetrics) ?? null
    }
  }

  const live = liveMetrics !== null
  return {
    activeSessions: live ? liveMetrics.active_sessions : (summary?.kpi.active_sessions ?? 0),
    totalCalls: live ? liveMetrics.summary.total_calls : (summary?.kpi.total_calls ?? 0),
    successRate: live ? liveMetrics.summary.success_rate : (summary?.kpi.success_rate ?? 100.0),
    p50: live ? liveMetrics.summary.p50_latency : (summary?.kpi.p50_latency ?? 0),
    p99: live ? liveMetrics.summary.p99_latency : (summary?.kpi.p99_latency ?? 0),
    graphCalls:
      liveMetrics?.summary.graph !== undefined
        ? liveMetrics.summary.graph.total_calls
        : (summary?.graph_kpi?.total_calls ?? 0),
    graphSuccessRate:
      liveMetrics?.summary.graph !== undefined
        ? liveMetrics.summary.graph.success_rate
        : (summary?.graph_kpi?.success_rate ?? 100.0),
  }
}

/** Prefer the live error buffer; otherwise the summary's recent_errors. */
export function overlayErrors(
  arg1: AnalyticsSummaryResponse | LiveMetrics | null | undefined,
  arg2: LiveMetrics | AnalyticsSummaryResponse | null | undefined,
  nowMs = Date.now(),
): RecentError[] {
  let summary: AnalyticsSummaryResponse | undefined
  let liveMetrics: LiveMetrics | null = null

  if (arg1 && typeof arg1 === "object" && "recent_errors" in arg1 && "relay" in arg1) {
    liveMetrics = arg1 as unknown as LiveMetrics
    summary = (arg2 as unknown as AnalyticsSummaryResponse) ?? undefined
  } else {
    summary = (arg1 as unknown as AnalyticsSummaryResponse) ?? undefined
    liveMetrics = (arg2 as unknown as LiveMetrics) ?? null
  }

  if (liveMetrics?.recent_errors && liveMetrics.recent_errors.length > 0) {
    return liveMetrics.recent_errors.map((e) => ({
      code: String(e.status_code || e.error_type || "500"),
      src: e.plugin_id,
      msg: `${e.tool} · ${e.error_type}`,
      when: formatErrorWhen(e.timestamp, nowMs),
    }))
  }
  return summary?.recent_errors ?? []
}
