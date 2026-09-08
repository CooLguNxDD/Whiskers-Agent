/**
 * Analytics API
 *
 * Tool-call telemetry summary + WS ticket via OperationCatalog
 * (owner `api.analytics`).
 */

import { catalogClient } from "./catalogClient"

export interface AnalyticsKpi {
  total_calls: number
  success_rate: number
  p50_latency: number
  p99_latency: number
  active_sessions: number
}

/**
 * Graph-run KPI — the feature="graph" axis (one row per whole agent run),
 * distinct from the MCP tool-call KPI above (tool calls dispatched *inside*
 * a run are still MCP calls, correlated via parent_run_id, not summed here).
 */
export interface GraphKpi {
  total_calls: number
  success_rate: number
  p50_latency: number
  p99_latency: number
}

export interface AnalyticsSeries {
  /** Legacy plugin-heuristic breakdown — kept for backward compat. */
  core: number[]
  extensions: number[]
  other: number[]
  /** Primary product axis (mcp vs graph) — see GraphKpi. */
  mcp: number[]
  graph: number[]
}

export interface TopTool {
  name: string
  calls: number
  trend: number[]
  p99: string
}

export interface RecentError {
  code: string
  src: string
  msg: string
  when: string
}

export interface TopModel {
  name: string
  calls: number
  pct: number
}

export interface AnalyticsSummaryResponse {
  kpi: AnalyticsKpi
  graph_kpi: GraphKpi
  series: AnalyticsSeries
  top_tools: TopTool[]
  recent_errors: RecentError[]
  top_models: TopModel[]
}

export interface AnalyticsWsTicketResponse {
  ws_url: string
  ws_ticket: string
}

/** One fish-tank visitor ask turn (plugins/portfolio_plugin/ask/telemetry.py). */
export interface AskTurn {
  run_id: string
  subject: string | null
  visitor_session_id: string | null
  question: string
  intent: string | null
  view: string | null
  focus_slug: string | null
  highlight_slugs: string[] | null
  add_slugs: string[] | null
  ok: boolean
  error_type: string | null
  latency_ms: number | null
  created_at: string | null
}

export interface AskTurnsResponse {
  status: string
  turns: AskTurn[]
  count: number
}

/**
 * Retrieve aggregated analytics summary for a time range.
 */
export function getAnalyticsSummary(range: string): Promise<AnalyticsSummaryResponse> {
  return catalogClient.analytics.summary(range)
}

/**
 * Mint a short-lived ticket to connect to the analytics WS stream.
 */
export function getAnalyticsWsTicket(): Promise<AnalyticsWsTicketResponse> {
  return catalogClient.analytics.wsTicket()
}

/**
 * Recent fish-tank visitor ask turns (session-gated, tenant-scoped).
 */
export function getAskTurns(limit = 50, intent?: string): Promise<AskTurnsResponse> {
  return catalogClient.analytics.askTurns(limit, intent)
}
