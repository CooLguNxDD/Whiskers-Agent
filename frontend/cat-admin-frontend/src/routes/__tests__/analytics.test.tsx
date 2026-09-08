/**
 * Analytics Route Unit Tests
 *
 * Verifies WS-driven invalidate-on-change for the historical analytics summary query,
 * and tests multi-tab navigation and filtering capabilities.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

import { AnalyticsPage } from "@/components/analytics/AnalyticsPage"
import { McpGraphChart } from "@/components/analytics/charts/McpGraphChart"
import {
  nextSeriesVisibility,
  peakBucket,
  penetration,
  toTrafficRows,
} from "@/components/analytics/series"
import type { LiveMetrics } from "@/hooks/useAnalytics"

const { mockLiveMetrics, useNavigateMock } = vi.hoisted(() => ({
  mockLiveMetrics: vi.fn(),
  useNavigateMock: vi.fn(),
}))

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<Record<string, any>>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  }
})

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...original,
    useNavigate: () => useNavigateMock,
  }
})

const ASK_TURNS = [
  {
    run_id: "run-1",
    question: "how does portfolio work?",
    intent: "focus_fish",
    ok: true,
    latency_ms: 85,
    visitor_session_id: "vis-123",
    created_at: "2026-08-27T15:00:00Z",
  },
  {
    run_id: "run-2",
    question: "tell me about devops",
    intent: "discover",
    ok: true,
    latency_ms: 110,
    visitor_session_id: "vis-456",
    created_at: "2026-08-27T15:05:00Z",
  },
]

vi.mock("@/hooks/useAnalytics", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/hooks/useAnalytics")>()
  return {
    ...original,
    useAnalyticsSummary: vi.fn(() => ({
      data: {
        kpi: {
          total_calls: 100,
          success_rate: 100,
          p50_latency: 24,
          p99_latency: 142,
          active_sessions: 2,
        },
        graph_kpi: {
          total_calls: 15,
          success_rate: 100,
          p50_latency: 500,
          p99_latency: 1200,
        },
        series: {
          core: Array(24).fill(1),
          extensions: Array(24).fill(0),
          other: Array(24).fill(0),
          mcp: Array.from({ length: 24 }, (_, i) => (i === 12 ? 40 : 10)),
          graph: Array.from({ length: 24 }, (_, i) => (i === 12 ? 4 : 1)),
        },
        top_tools: [
          { name: "test_tool_a", calls: 50, trend: [1, 2, 3], p99: "10ms" },
          { name: "test_tool_b", calls: 30, trend: [2, 3, 4], p99: "25ms" },
        ],
        recent_errors: [
          { code: "429", src: "test_plugin", msg: "rate limit exceeded", when: "5m ago" },
        ],
        top_models: [
          { name: "claude-3.7-sonnet", calls: 80, pct: 80 },
        ],
      },
    })),
    useAskTurns: vi.fn((_limit?: number, intent?: string) => {
      const turns = intent ? ASK_TURNS.filter((t) => t.intent === intent) : ASK_TURNS
      return {
        data: {
          status: "ok",
          count: turns.length,
          turns,
        },
        isError: false,
      }
    }),
    useLiveMetrics: mockLiveMetrics,
  }
})

vi.mock("@/components/analytics/charts/StackedTrafficChart", () => ({
  StackedTrafficChart: () => <div data-testid="stacked-chart" />,
}))

vi.mock("@/components/shell/AppShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}))

vi.mock("@/components/shell/Icons", () => {
  const IconStub = () => <span />
  return {
    Activity: IconStub,
    Spark: IconStub,
    Clock: IconStub,
    Globe: IconStub,
    Search: IconStub,
    Filter: IconStub,
    Shield: IconStub,
    Cube: IconStub,
    Download: IconStub,
    Chat: IconStub,
    Sliders: IconStub,
  }
})

/** Build a minimal live metrics snapshot for WS mock data. */
function makeLiveMetrics(totalCalls: number): LiveMetrics {
  return {
    active_sessions: 0,
    relay: {
      bytes_console: 0,
      bytes_ext: 0,
      frames: 0,
      session_open: 0,
      session_close: 0,
    },
    summary: {
      total_calls: totalCalls,
      success_rate: 100,
      p50_latency: 0,
      p99_latency: 0,
    },
    recent_errors: [],
    minute_buckets: [],
  }
}

/** Create a QueryClient wrapper with an invalidateQueries spy. */
function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { invalidateSpy, wrapper }
}

describe("AnalyticsPage summary invalidation", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLiveMetrics.mockReturnValue({ metrics: null, status: "connecting" })
    useNavigateMock.mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("does not invalidate on initial mount while live metrics are null", () => {
    const { invalidateSpy, wrapper } = createWrapper()
    render(<AnalyticsPage />, { wrapper })

    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it("does not invalidate when live metrics first connect without a prior total_calls baseline", () => {
    const { invalidateSpy, wrapper } = createWrapper()
    const { rerender } = render(<AnalyticsPage />, { wrapper })

    mockLiveMetrics.mockReturnValue({
      metrics: makeLiveMetrics(100),
      status: "connected",
    })
    rerender(<AnalyticsPage />)

    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it("invalidates the summary query when total_calls changes", () => {
    const { invalidateSpy, wrapper } = createWrapper()
    const { rerender } = render(<AnalyticsPage />, { wrapper })

    mockLiveMetrics.mockReturnValue({
      metrics: makeLiveMetrics(100),
      status: "connected",
    })
    rerender(<AnalyticsPage />)

    mockLiveMetrics.mockReturnValue({
      metrics: makeLiveMetrics(101),
      status: "connected",
    })
    rerender(<AnalyticsPage />)

    expect(invalidateSpy).toHaveBeenCalledTimes(1)
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["analyticsSummary", "24h"],
    })
  })

  it("throttles invalidation to once per 10 seconds", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-06-23T12:00:00.000Z"))

    const { invalidateSpy, wrapper } = createWrapper()
    const { rerender } = render(<AnalyticsPage />, { wrapper })

    mockLiveMetrics.mockReturnValue({
      metrics: makeLiveMetrics(100),
      status: "connected",
    })
    rerender(<AnalyticsPage />)

    mockLiveMetrics.mockReturnValue({
      metrics: makeLiveMetrics(101),
      status: "connected",
    })
    rerender(<AnalyticsPage />)
    expect(invalidateSpy).toHaveBeenCalledTimes(1)

    mockLiveMetrics.mockReturnValue({
      metrics: makeLiveMetrics(102),
      status: "connected",
    })
    rerender(<AnalyticsPage />)
    expect(invalidateSpy).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(10_001)

    mockLiveMetrics.mockReturnValue({
      metrics: makeLiveMetrics(103),
      status: "connected",
    })
    rerender(<AnalyticsPage />)
    expect(invalidateSpy).toHaveBeenCalledTimes(2)
    expect(invalidateSpy).toHaveBeenLastCalledWith({
      queryKey: ["analyticsSummary", "24h"],
    })
  })
})

describe("AnalyticsPage multi-tab navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLiveMetrics.mockReturnValue({ metrics: null, status: "connecting" })
    useNavigateMock.mockImplementation(() => undefined)
  })

  it("renders all 4 tabs and starts on Overview", () => {
    const { wrapper } = createWrapper()
    render(<AnalyticsPage />, { wrapper })

    expect(screen.getByRole("tab", { name: /Overview/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /Tools & Traffic/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /Ask Turns/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /Errors & Health/i })).toBeInTheDocument()

    expect(screen.getByText("Primary product axis")).toBeInTheDocument()
    expect(screen.getByText("Top Active Tools")).toBeInTheDocument()
  })

  it("honors tab + range search params", () => {
    const { wrapper } = createWrapper()
    render(<AnalyticsPage tab="tools" range="7d" />, { wrapper })

    expect(screen.getByText("Tool Operations")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "7d" })).toHaveAttribute("aria-pressed", "true")
  })

  it("switches to Tools & Traffic tab and filters tools", () => {
    const { wrapper } = createWrapper()
    const { rerender } = render(<AnalyticsPage tab="overview" />, { wrapper })

    fireEvent.click(screen.getByRole("tab", { name: /Tools & Traffic/i }))
    expect(useNavigateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        to: "/analytics",
        search: expect.objectContaining({ tab: "tools" }),
      }),
    )

    rerender(<AnalyticsPage tab="tools" />)
    expect(screen.getByText("Tool Operations")).toBeInTheDocument()
    expect(screen.getByText("test_tool_a")).toBeInTheDocument()
    expect(screen.getByText("test_tool_b")).toBeInTheDocument()

    const searchInput = screen.getByPlaceholderText("Search tools by name…")
    fireEvent.change(searchInput, { target: { value: "tool_a" } })

    expect(screen.getByText("test_tool_a")).toBeInTheDocument()
    expect(screen.queryByText("test_tool_b")).not.toBeInTheDocument()
  })

  it("switches to Ask Turns tab and displays visitor queries", () => {
    const { wrapper } = createWrapper()
    const { rerender } = render(<AnalyticsPage tab="overview" />, { wrapper })

    fireEvent.click(screen.getByRole("tab", { name: /Ask Turns/i }))
    expect(useNavigateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        search: expect.objectContaining({ tab: "ask_turns" }),
      }),
    )

    rerender(<AnalyticsPage tab="ask_turns" />)
    expect(screen.getByText("Visitor Ask Turns")).toBeInTheDocument()
    expect(screen.getByText("how does portfolio work?")).toBeInTheDocument()
    expect(screen.getByText("tell me about devops")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "focus_fish" }))
    expect(screen.getByText("how does portfolio work?")).toBeInTheDocument()
    expect(screen.queryByText("tell me about devops")).not.toBeInTheDocument()
  })

  it("switches to Errors & Health tab and displays diagnostics", () => {
    const { wrapper } = createWrapper()
    const { rerender } = render(<AnalyticsPage tab="overview" />, { wrapper })

    fireEvent.click(screen.getByRole("tab", { name: /Errors & Health/i }))
    expect(useNavigateMock).toHaveBeenCalledWith(
      expect.objectContaining({
        search: expect.objectContaining({ tab: "errors" }),
      }),
    )

    rerender(<AnalyticsPage tab="errors" />)
    expect(screen.getByText("Error Diagnostics & Trace Log")).toBeInTheDocument()
    expect(screen.getByText("rate limit exceeded")).toBeInTheDocument()
    expect(screen.getByText("test_plugin")).toBeInTheDocument()
  })
})

describe("McpGraphChart series toggles", () => {
  it("keeps at least one series visible", () => {
    render(
      <McpGraphChart
        range="24h"
        wsStatus="connected"
        series={{
          core: Array(24).fill(0),
          extensions: Array(24).fill(0),
          other: Array(24).fill(0),
          mcp: Array(24).fill(8),
          graph: Array(24).fill(2),
        }}
      />,
    )

    const mcp = screen.getByRole("button", { name: "MCP Calls" })
    const graph = screen.getByRole("button", { name: "Graph Runs" })
    expect(mcp).toHaveAttribute("aria-pressed", "true")
    expect(graph).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(mcp)
    expect(mcp).toHaveAttribute("aria-pressed", "false")
    expect(graph).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(graph)
    expect(mcp).toHaveAttribute("aria-pressed", "false")
    expect(graph).toHaveAttribute("aria-pressed", "true")
  })
})

describe("series.ts transforms", () => {
  it("toTrafficRows maps 24 parallel arrays onto labeled rows", () => {
    const mcp = Array.from({ length: 24 }, (_, i) => i)
    const graph = Array.from({ length: 24 }, (_, i) => i * 2)
    const rows = toTrafficRows(
      { mcp, graph, core: mcp, extensions: graph, other: Array(24).fill(0) },
      "24h",
    )
    expect(rows).toHaveLength(24)
    expect(rows[0]).toMatchObject({ label: "00:00", mcp: 0, graph: 0 })
    expect(rows[12]).toMatchObject({ label: "12:00", mcp: 12, graph: 24 })
  })

  it("peakBucket returns the max bucket and its label", () => {
    expect(peakBucket([1, 9, 3], "24h")).toEqual({ value: 9, label: "01:00" })
  })

  it("penetration is graph/mcp percent with a zero-safe divisor", () => {
    expect(penetration(15, 100)).toBe(15)
    expect(penetration(1, 0)).toBe(0)
  })

  it("nextSeriesVisibility refuses both-off", () => {
    expect(nextSeriesVisibility({ mcp: false, graph: true }, "graph")).toEqual({
      mcp: false,
      graph: true,
    })
    expect(nextSeriesVisibility({ mcp: true, graph: true }, "mcp")).toEqual({
      mcp: false,
      graph: true,
    })
  })
})
