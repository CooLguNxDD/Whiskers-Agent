/**
 * Stacked plugin-heuristic traffic (core / extensions / other).
 */
import React from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart"
import { STACKED_PLUGIN_CONFIG } from "../analyticsConfig"
import { sparseTickLabels, toStackedRows } from "./series"

interface StackedTrafficChartProps {
  series: { core?: number[]; extensions?: number[]; other?: number[] } | undefined
  range: string
}

export const StackedTrafficChart = React.memo(function StackedTrafficChart({
  series,
  range,
}: StackedTrafficChartProps) {
  const rows = toStackedRows(series, range)
  const ticks = sparseTickLabels(rows)

  return (
    <ChartContainer
      config={STACKED_PLUGIN_CONFIG}
      className="ct-chart-svg aspect-auto h-full min-h-[200px] w-full"
      initialDimension={{ width: 720, height: 200 }}
      aria-label="Tool calls by plugin group over time"
    >
      <AreaChart data={rows} accessibilityLayer margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid vertical={false} strokeDasharray="2 4" />
        <XAxis
          dataKey="label"
          ticks={ticks}
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
        />
        <YAxis hide />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Area
          type="monotone"
          dataKey="core"
          stackId="plugin"
          fill="var(--color-core)"
          stroke="var(--color-core)"
          fillOpacity={0.45}
          strokeWidth={1.5}
          dot={false}
        />
        <Area
          type="monotone"
          dataKey="extensions"
          stackId="plugin"
          fill="var(--color-extensions)"
          stroke="var(--color-extensions)"
          fillOpacity={0.4}
          strokeWidth={1.3}
          dot={false}
        />
        <Area
          type="monotone"
          dataKey="other"
          stackId="plugin"
          fill="var(--color-other)"
          stroke="var(--color-other)"
          fillOpacity={0.35}
          strokeWidth={1.3}
          dot={false}
        />
      </AreaChart>
    </ChartContainer>
  )
})
