import { useMemo } from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { CORE_PLUGIN_DISPLAY_NAME } from "@/constants/plugins"
import type { AnalyticsSeries } from "@/api/analytics"
import { toTrafficRows } from "@/components/analytics/series"

const pluginChartConfig = {
  core: { label: CORE_PLUGIN_DISPLAY_NAME, color: "var(--chart-1)" },
  extensions: { label: "extensions", color: "var(--chart-2)" },
  other: { label: "other", color: "var(--chart-4)" },
} satisfies ChartConfig

/**
 * Stacked plugin-group AreaChart (tools tab only). Shared Y-axis because
 * core / extensions / other are additive.
 */
export function PluginStackedChart({
  series,
  range,
}: {
  series: AnalyticsSeries | undefined
  range: string
}) {
  const rows = useMemo(() => toTrafficRows(series, range), [series, range])

  return (
    <Card>
      <CardHeader className="border-b">
        <CardDescription>Traffic · stacked by plugin</CardDescription>
        <CardTitle>
          Requests per interval
          <span className="ml-2 font-mono text-xs font-medium text-muted-foreground">· {range}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ChartContainer
          config={pluginChartConfig}
          className="aspect-auto h-[220px] min-h-[220px] w-full"
          aria-label="Tool calls by plugin group over time"
        >
          <AreaChart data={rows} margin={{ left: 8, right: 8, top: 8 }}>
            <defs>
              <linearGradient id="fillCore" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-core)" stopOpacity={0.7} />
                <stop offset="95%" stopColor="var(--color-core)" stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="fillExtensions" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-extensions)" stopOpacity={0.55} />
                <stop offset="95%" stopColor="var(--color-extensions)" stopOpacity={0.04} />
              </linearGradient>
              <linearGradient id="fillOther" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-other)" stopOpacity={0.45} />
                <stop offset="95%" stopColor="var(--color-other)" stopOpacity={0.03} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis tickLine={false} axisLine={false} width={40} />
            <ChartTooltip content={<ChartTooltipContent />} />
            <Area
              stackId="plugin"
              dataKey="core"
              type="natural"
              fill="url(#fillCore)"
              stroke="var(--color-core)"
              strokeWidth={1.5}
            />
            <Area
              stackId="plugin"
              dataKey="extensions"
              type="natural"
              fill="url(#fillExtensions)"
              stroke="var(--color-extensions)"
              strokeWidth={1.3}
            />
            <Area
              stackId="plugin"
              dataKey="other"
              type="natural"
              fill="url(#fillOther)"
              stroke="var(--color-other)"
              strokeWidth={1.3}
            />
          </AreaChart>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}
