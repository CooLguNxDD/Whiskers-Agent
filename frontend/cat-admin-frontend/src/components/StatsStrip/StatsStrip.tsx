/**
 * StatsStrip Component
 *
 * Provides a high-level overview of the MCP server's current status,
 * showing the total number of plugins, how many are enabled, and the system tier.
 */

import type { FC } from "react"
import { cn } from "@/lib/utils"
import { usePluginsQuery, useSystemTier } from "@/hooks/usePlugins"

export interface StatsStripProps {
  className?: string
}

/**
 * Internal component for rendering a single statistic cell.
 */
const StatCell: FC<{ label: string; value: number | string }> = ({ label, value }) => (
  <div className="flex flex-col items-center gap-0.5">
    <span className="text-2xl font-bold tabular-nums">{value}</span>
    <span className="text-xs text-muted-foreground">{label}</span>
  </div>
)

/**
 * Renders the statistics strip with aggregate plugin data.
 */
const StatsStrip: FC<StatsStripProps> = ({ className }) => {
  const { data, isPending } = usePluginsQuery()
  const plugins = data?.plugins
  const systemTier = useSystemTier()

  const total = plugins?.length ?? 0
  const enabled = plugins?.filter((m) => m.enabled).length ?? 0

  if (isPending) {
    return (
      <div className={cn("grid grid-cols-3 gap-4 rounded-lg border bg-card p-4 animate-pulse", className)}>
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-10 rounded bg-muted" />
        ))}
      </div>
    )
  }

  return (
    <div className={cn("grid grid-cols-3 gap-4 rounded-lg border bg-card p-4", className)}>
      <StatCell label="Total" value={total} />
      <StatCell label="Enabled" value={enabled} />
      <StatCell label="Tier" value={systemTier ?? "LITE"} />
    </div>
  )
}

/**
 * StatsStrip Component.
 * Renders the UI and handles state for the StatsStrip feature.
 */
export default StatsStrip
