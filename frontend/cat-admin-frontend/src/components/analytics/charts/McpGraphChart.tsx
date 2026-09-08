import React, { useMemo, useState } from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { Button } from "@/components/ui/button"
import type { AnalyticsSeries } from "@/api/analytics"
import {
  nextSeriesVisibility,
  peakBucket,
  penetration,
  toTrafficRows,
  type SeriesVisibility,
} from "@/components/analytics/series"

const mcpGraphConfig = {
  mcp: { label: "MCP Calls", color: "var(--chart-1)" },
  graph: { label: "Graph Runs", color: "var(--chart-4)" },
} satisfies ChartConfig

/**
 * Dual-axis MCP vs graph AreaChart. Graph runs stay on a dedicated right
 * axis so a handful of runs remain legible next to thousands of MCP calls.
 */
export const McpGraphChart = React.memo(function McpGraphChart({
  series,
  range,
  wsStatus,
}: {
  series: AnalyticsSeries | undefined
  range: string
  wsStatus: string
}) {
  const [visible, setVisible] = useState<SeriesVisibility>({ mcp: true, graph: true })
  const rows = useMemo(() => toTrafficRows(series, range), [series, range])

  const peak = useMemo(() => peakBucket(rows.map((r) => r.mcp), range), [rows, range])
  const graphPenetration = useMemo(() => {
    const mcp = rows.reduce((sum, r) => sum + r.mcp, 0)
    const graph = rows.reduce((sum, r) => sum + r.graph, 0)
    return penetration(graph, mcp)
  }, [rows])

  const toggle = (key: keyof SeriesVisibility) => {
    setVisible((cur) => nextSeriesVisibility(cur, key))
  }

  return (
    <Card>
      <CardHeader className="border-b">
        <CardDescription>Traffic · MCP vs Graph</CardDescription>
        <CardTitle>
          Primary product axis
          <span className="ml-2 font-mono text-xs font-medium text-muted-foreground">· {range}</span>
        </CardTitle>
        <CardAction>
          <div className="flex gap-1">
            <Button
              type="button"
              size="sm"
              variant={visible.mcp ? "default" : "ghost"}
              aria-pressed={visible.mcp}
              onClick={() => toggle("mcp")}
            >
              MCP Calls
            </Button>
            <Button
              type="button"
              size="sm"
              variant={visible.graph ? "secondary" : "ghost"}
              aria-pressed={visible.graph}
              onClick={() => toggle("graph")}
            >
              Graph Runs
            </Button>
          </div>
        </CardAction>
      </CardHeader>
      <CardContent>
        <ChartContainer
          config={mcpGraphConfig}
          className="aspect-auto h-[220px] min-h-[220px] w-full"
          aria-label="MCP vs graph traffic over time"
        >
          <AreaChart data={rows} accessibilityLayer margin={{ left: 8, right: 8, top: 8 }}>
            <defs>
              <linearGradient id="fillMcp" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-mcp)" stopOpacity={0.55} />
                <stop offset="95%" stopColor="var(--color-mcp)" stopOpacity={0.04} />
              </linearGradient>
              <linearGradient id="fillGraph" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-graph)" stopOpacity={0.45} />
                <stop offset="95%" stopColor="var(--color-graph)" stopOpacity={0.04} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} />
            {visible.mcp && (
              <YAxis yAxisId="mcp" orientation="left" tickLine={false} axisLine={false} width={40} />
            )}
            {visible.graph && (
              <YAxis yAxisId="graph" orientation="right" tickLine={false} axisLine={false} width={40} />
            )}
            <ChartTooltip
              content={
                <ChartTooltipContent
                  extraRatioKey={{ dividend: "graph", divisor: "mcp", label: "penetration" }}
                />
              }
            />
            {visible.mcp && (
              <Area
                yAxisId="mcp"
                dataKey="mcp"
                type="natural"
                fill="url(#fillMcp)"
                stroke="var(--color-mcp)"
                strokeWidth={1.5}
              />
            )}
            {visible.graph && (
              <Area
                yAxisId="graph"
                dataKey="graph"
                type="natural"
                fill="url(#fillGraph)"
                stroke="var(--color-graph)"
                strokeWidth={1.6}
              />
            )}
          </AreaChart>
        </ChartContainer>
      </CardContent>
      <CardFooter className="gap-4 text-xs text-muted-foreground font-mono flex-wrap">
        <span>
          Peak MCP {peak.value.toLocaleString()} calls/h at {peak.label}
        </span>
        <span>Graph penetration {graphPenetration.toFixed(1)}%</span>
        <span>WS {wsStatus}</span>
      </CardFooter>
    </Card>
  )
})
