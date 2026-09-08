import { Activity, Spark, Clock, Globe } from "@/components/shell/Icons"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table } from "@/components/Table/Table"
import type { Column } from "@/components/Table/useSort"
import type { AnalyticsSummaryResponse, TopTool } from "@/api/analytics"
import type { OverlayKpis } from "@/components/analytics/overlay"
import type { RecentError } from "@/api/analytics"
import { KpiStrip } from "@/components/analytics/KpiStrip"
import { McpGraphChart } from "@/components/analytics/charts/McpGraphChart"
import { TinySpark } from "@/components/analytics/charts/TinySpark"
import type { AnalyticsTab } from "@/components/analytics/search"

const topToolColumns: Column<TopTool>[] = [
  {
    key: "name",
    header: "Tool",
    render: (row) => <span className="tool-name font-mono">{row.name}</span>,
  },
  {
    key: "calls",
    header: "Calls",
    align: "right",
    sortable: true,
    render: (row) => <span className="font-mono">{row.calls.toLocaleString()}</span>,
  },
  {
    key: "trend",
    header: "Trend",
    align: "right",
    render: (row) => <TinySpark values={row.trend} />,
  },
  {
    key: "p99",
    header: "p99",
    align: "right",
    render: (row) => <span className="font-mono">{row.p99}</span>,
  },
]

/**
 * Overview tab: KPI strip, MCP/graph hero chart, top-4 tools, health panel.
 */
export function OverviewTab({
  range,
  summary,
  kpis,
  errors,
  askTurnCount,
  wsStatus,
  onSelectTab,
}: {
  range: string
  summary: AnalyticsSummaryResponse | undefined
  kpis: OverlayKpis
  errors: RecentError[]
  askTurnCount: number
  wsStatus: string
  onSelectTab: (tab: AnalyticsTab) => void
}) {
  const topTools = summary?.top_tools?.slice(0, 4) ?? []

  return (
    <div className="flex flex-col gap-4">
      <KpiStrip
        columns={5}
        items={[
          {
            key: "mcp",
            label: `MCP calls · ${range}`,
            value: kpis.totalCalls.toLocaleString(),
            sub: "/ range",
            delta: "historic",
            icon: <Activity width="16" height="16" />,
            iconClass: "is-pink",
          },
          {
            key: "graph",
            label: `Graph runs · ${range}`,
            value: kpis.graphCalls.toLocaleString(),
            sub: `/ ${kpis.graphSuccessRate.toFixed(0)}% ok`,
            delta: "historic",
            icon: <Activity width="16" height="16" />,
            iconClass: "is-cyan",
          },
          {
            key: "ok",
            label: "Success rate",
            value: kpis.successRate.toFixed(2),
            sub: "%",
            delta: "↑ stable",
            deltaUp: true,
            icon: <Spark width="16" height="16" />,
            iconClass: "is-neon",
          },
          {
            key: "lat",
            label: "Latency",
            value: kpis.p50,
            sub: `/ ${kpis.p99} ms`,
            delta: "p50 / p99",
            icon: <Clock width="16" height="16" />,
          },
          {
            key: "sess",
            label: "Active sessions",
            value: kpis.activeSessions,
            sub: "connected",
            delta: "live",
            deltaUp: true,
            icon: <Globe width="16" height="16" />,
            iconClass: "is-cyan",
          },
        ]}
      />

      <McpGraphChart series={summary?.series} range={range} wsStatus={wsStatus} />

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="border-b flex-row items-center justify-between">
            <CardTitle>Top Active Tools</CardTitle>
            <Button type="button" variant="ghost" size="sm" onClick={() => onSelectTab("tools")}>
              Explore all tools →
            </Button>
          </CardHeader>
          <CardContent className="px-0">
            <Table
              columns={topToolColumns}
              rows={topTools}
              rowKey={(r) => r.name}
              emptyMessage="No tool activity in this range."
            />
          </CardContent>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader className="border-b flex-row items-center justify-between">
              <CardTitle>System Health & Telemetry</CardTitle>
              <Badge variant={errors.length > 0 ? "destructive" : "secondary"}>
                {errors.length > 0 ? `${errors.length} errors` : "Nominal"}
              </Badge>
            </CardHeader>
            <CardContent className="flex flex-col gap-3.5">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Live Telemetry Stream</span>
                <span className="font-mono uppercase">{wsStatus}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Active Relay Sessions</span>
                <span className="font-mono">{kpis.activeSessions} active</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Fish Tank Ask Turns</span>
                <Button type="button" variant="ghost" size="sm" onClick={() => onSelectTab("ask_turns")}>
                  {askTurnCount} turns →
                </Button>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Recent Error Diagnostics</span>
                <Button type="button" variant="ghost" size="sm" onClick={() => onSelectTab("errors")}>
                  {errors.length} in range →
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b flex-row items-center justify-between">
              <CardTitle>Top Callers Preview</CardTitle>
              <Button type="button" variant="ghost" size="sm" onClick={() => onSelectTab("tools")}>
                View details →
              </Button>
            </CardHeader>
            <CardContent>
              {summary?.top_models && summary.top_models.length > 0 ? (
                summary.top_models.slice(0, 2).map(({ name, calls, pct }) => (
                  <div key={name} className="mb-2.5">
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="font-mono">{name}</span>
                      <span className="font-mono text-muted-foreground">
                        {calls.toLocaleString()} · {pct}%
                      </span>
                    </div>
                    <div className="ct-usage-bar">
                      <div className="ct-usage-fill is-neon" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                ))
              ) : (
                <div className="py-3 text-center text-muted-foreground">No callers recorded.</div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
