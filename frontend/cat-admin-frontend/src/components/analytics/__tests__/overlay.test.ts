import { describe, it, expect } from "vitest"
import { formatErrorWhen, overlayErrors, overlayKpis } from "../overlay"
import type { LiveMetrics } from "@/hooks/useAnalytics"

function live(total: number, extras: Partial<LiveMetrics> = {}): LiveMetrics {
  return {
    active_sessions: 3,
    relay: { bytes_console: 0, bytes_ext: 0, frames: 0, session_open: 0, session_close: 0 },
    summary: { total_calls: total, success_rate: 99, p50_latency: 10, p99_latency: 20 },
    recent_errors: [],
    minute_buckets: [],
    ...extras,
  }
}

describe("overlayKpis", () => {
  it("falls back to summary when WS is null", () => {
    const k = overlayKpis(null, {
      kpi: { total_calls: 5, success_rate: 80, p50_latency: 1, p99_latency: 2, active_sessions: 1 },
      graph_kpi: { total_calls: 7, success_rate: 90, p50_latency: 0, p99_latency: 0 },
      series: { core: [], extensions: [], other: [], mcp: [], graph: [] },
      top_tools: [],
      recent_errors: [],
      top_models: [],
    })
    expect(k.totalCalls).toBe(5)
    expect(k.graphCalls).toBe(7)
    expect(k.activeSessions).toBe(1)
  })

  it("prefers live totals when connected", () => {
    const k = overlayKpis(live(42), undefined)
    expect(k.totalCalls).toBe(42)
    expect(k.activeSessions).toBe(3)
  })
})

describe("formatErrorWhen", () => {
  const now = Date.parse("2026-08-28T12:00:00.000Z")
  it("buckets just now / minutes / hours", () => {
    expect(formatErrorWhen(now / 1000, now)).toBe("just now")
    expect(formatErrorWhen(now / 1000 - 120, now)).toBe("2m ago")
    expect(formatErrorWhen(now / 1000 - 7200, now)).toBe("2h ago")
  })
})

describe("overlayErrors", () => {
  it("maps live errors when present", () => {
    const now = Date.parse("2026-08-28T12:00:00.000Z")
    const snap = live(1, {
      recent_errors: [{
        tool: "run_graph",
        plugin_id: "core",
        error_type: "Timeout",
        status_code: 504,
        timestamp: now / 1000,
      }],
    })
    const rows = overlayErrors(snap, undefined, now)
    expect(rows).toEqual([{
      code: "504",
      src: "core",
      msg: "run_graph · Timeout",
      when: "just now",
    }])
  })
})
