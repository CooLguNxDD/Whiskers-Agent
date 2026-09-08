/**
 * useAnalytics Hooks
 *
 * React hooks binding the UI to the TanStack Query historical analytics API and
 * the real-time WebSocket telemetry stream.
 */

import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { getAnalyticsSummary, getAnalyticsWsTicket, getAskTurns } from "@/api/analytics"
import { useAuthedQueryEnabled } from "./useAuthedQuery"

interface LiveKpiBlock {
  total_calls: number
  success_rate: number
  p50_latency: number
  p99_latency: number
}

export interface LiveMetrics {
  active_sessions: number
  relay: {
    bytes_console: number
    bytes_ext: number
    frames: number
    session_open: number
    session_close: number
  }
  summary: LiveKpiBlock & {
    /** Product axis split — optional so an in-flight older snapshot shape still parses. */
    mcp?: LiveKpiBlock
    graph?: LiveKpiBlock
  }
  recent_errors: Array<{
    tool: string
    plugin_id: string
    error_type: string
    status_code: number | null
    timestamp: number
  }>
  minute_buckets: Array<{
    timestamp: number
    tool_calls: Record<string, number>
    relay_bytes: number
  }>
}

/** Capped exponential backoff with jitter for WS reconnects (ms). */
export function nextReconnectDelay(attempt: number): number {
  const base = Math.min(30_000, 1_000 * 2 ** attempt)
  return base + Math.floor(Math.random() * 1_000)
}

/** Shared TanStack Query key for the historical analytics summary. */
export function analyticsSummaryQueryKey(range: string) {
  return ["analyticsSummary", range] as const
}

/**
 * Query the historical aggregated analytics summary.
 */
export function useAnalyticsSummary(range: string) {
  const enabled = useAuthedQueryEnabled()
  return useQuery({
    queryKey: analyticsSummaryQueryKey(range),
    queryFn: () => getAnalyticsSummary(range),
    staleTime: 10_000,
    enabled,
  })
}

/**
 * Invalidate the summary query when live total_calls changes, throttled to 10s.
 */
export function useInvalidateSummaryOnLiveChange(range: string, liveMetrics: LiveMetrics | null) {
  const queryClient = useQueryClient()
  const lastInvalidationRef = useRef(0)
  const prevTotalCallsRef = useRef<number | null>(null)

  useEffect(() => {
    if (liveMetrics === null) return

    const totalCalls = liveMetrics.summary.total_calls
    const prev = prevTotalCallsRef.current

    if (prev !== null && prev !== totalCalls) {
      const now = Date.now()
      if (now - lastInvalidationRef.current >= 10_000) {
        void queryClient.invalidateQueries({ queryKey: analyticsSummaryQueryKey(range) })
        lastInvalidationRef.current = now
      }
    }

    prevTotalCallsRef.current = totalCalls
  }, [liveMetrics, range, queryClient])
}

/**
 * Query recent fish-tank visitor ask turns (portfolio_ask_turns).
 */
export function useAskTurns(limit = 50, intent?: string) {
  const enabled = useAuthedQueryEnabled()
  return useQuery({
    queryKey: ["askTurns", limit, intent],
    queryFn: () => getAskTurns(limit, intent),
    staleTime: 10_000,
    enabled,
  })
}

/**
 * Subscribe to the real-time WebSocket telemetry metrics stream.
 */
export function useLiveMetrics() {
  const [metrics, setMetrics] = useState<LiveMetrics | null>(null)
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("disconnected")

  useEffect(() => {
    let ws: WebSocket | null = null
    let active = true
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null
    let attempt = 0

    // Establish WebSocket connection using minted ticket
    async function connect() {
      if (!active) return
      setStatus("connecting")
      try {
        const ticketRes = await getAnalyticsWsTicket()
        if (!active) return

        const url = `${ticketRes.ws_url}?token=${encodeURIComponent(ticketRes.ws_ticket)}`
        ws = new WebSocket(url)

        ws.onopen = () => {
          if (!active) {
            ws?.close()
            return
          }
          setStatus("connected")
          attempt = 0
        }

        ws.onmessage = (ev) => {
          if (!active) return
          try {
            const data = JSON.parse(ev.data)
            setMetrics(data)
          } catch (err) {
            console.error("Failed to parse live analytics metrics snapshot:", err)
          }
        }

        ws.onclose = () => {
          ws = null
          if (!active) return
          setStatus("disconnected")
          reconnectTimeout = setTimeout(connect, nextReconnectDelay(attempt++))
        }

        ws.onerror = () => {
          if (ws) {
            const dying = ws
            ws = null
            dying.close()
          }
        }
      } catch (err) {
        console.error("Failed to initiate live analytics connection:", err)
        setStatus("disconnected")
        if (active) {
          reconnectTimeout = setTimeout(connect, nextReconnectDelay(attempt++))
        }
      }
    }

    connect()

    return () => {
      active = false
      if (ws) {
        const activeWs = ws
        ws = null
        activeWs.close()
      }
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout)
      }
    }
  }, [])

  return { metrics, status }
}
