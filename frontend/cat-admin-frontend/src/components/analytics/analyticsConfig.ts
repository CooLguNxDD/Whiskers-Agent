/**
 * Analytics dashboard config — ChartConfig palettes, ranges, KPI descriptors.
 */
import type { ChartConfig } from "@/components/ui/chart"
import { CORE_PLUGIN_DISPLAY_NAME } from "@/constants/plugins"

export const RANGE_OPTIONS = ["1h", "24h", "7d", "30d"] as const
export type AnalyticsRange = (typeof RANGE_OPTIONS)[number]

export const BUCKET_COUNT = 24

export const MCP_GRAPH_CONFIG = {
  mcp: { label: "MCP calls", color: "var(--chart-1)" },
  graph: { label: "Graph runs", color: "var(--chart-4)" },
} satisfies ChartConfig

export const STACKED_PLUGIN_CONFIG = {
  core: { label: CORE_PLUGIN_DISPLAY_NAME, color: "var(--chart-1)" },
  extensions: { label: "extensions", color: "var(--chart-2)" },
  other: { label: "other", color: "var(--chart-4)" },
} satisfies ChartConfig

/** Collect color strings from a ChartConfig for tests / lint-style checks. */
export function chartConfigColors(config: ChartConfig): string[] {
  const out: string[] = []
  for (const entry of Object.values(config)) {
    if (entry.color) out.push(entry.color)
    if (entry.theme) out.push(...Object.values(entry.theme))
  }
  return out
}
