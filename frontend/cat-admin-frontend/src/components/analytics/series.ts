/**
 * Pure analytics series helpers — 24-bucket API arrays → Recharts rows,
 * peak velocity, graph/MCP penetration, and series-toggle rules.
 */

/** Shared 24-bucket zero series — the analytics API always returns length 24. */
export const EMPTY_SERIES: number[] = Array(24).fill(0)

export type TrafficSeries = {
  core?: number[]
  extensions?: number[]
  other?: number[]
  mcp?: number[]
  graph?: number[]
}

export type TrafficRow = {
  label: string
  mcp: number
  graph: number
  core: number
  extensions: number
  other: number
}

export type SeriesVisibility = { mcp: boolean; graph: boolean }

export type SeriesKey = keyof SeriesVisibility

/** Bucket tick label for a 24-slot series and selected range. */
export function bucketLabel(index: number, range: string): string {
  if (range === "24h") return `${String(index).padStart(2, "0")}:00`
  if (range === "1h") return `${index * 2.5}m`
  return `B${index}`
}

function seriesOrEmpty(values: number[] | undefined): number[] {
  return values && values.length ? values : EMPTY_SERIES
}

/**
 * Flatten 24 parallel arrays into Recharts `{ label, mcp, graph, core, … }[]`.
 */
export function toTrafficRows(series: TrafficSeries | undefined, range: string): TrafficRow[] {
  const mcp = seriesOrEmpty(series?.mcp)
  const graph = seriesOrEmpty(series?.graph)
  const core = seriesOrEmpty(series?.core)
  const extensions = seriesOrEmpty(series?.extensions)
  const other = seriesOrEmpty(series?.other)
  const length = Math.max(mcp.length, graph.length, core.length, extensions.length, other.length, 24)

  return Array.from({ length }, (_, i) => ({
    label: bucketLabel(i, range),
    mcp: mcp[i] ?? 0,
    graph: graph[i] ?? 0,
    core: core[i] ?? 0,
    extensions: extensions[i] ?? 0,
    other: other[i] ?? 0,
  }))
}

/** Highest bucket in `values` plus its range-aware label. */
export function peakBucket(values: number[], range: string): { value: number; label: string } {
  if (!values.length) return { value: 0, label: bucketLabel(0, range) }
  let maxIndex = 0
  for (let i = 1; i < values.length; i += 1) {
    if ((values[i] ?? 0) > (values[maxIndex] ?? 0)) maxIndex = i
  }
  return { value: values[maxIndex] ?? 0, label: bucketLabel(maxIndex, range) }
}

/** Graph-run share of MCP volume as a percent. Zero divisor → 0. */
export function penetration(graph: number, mcp: number): number {
  if (mcp <= 0) return 0
  return (graph / mcp) * 100
}

/** Toggle a series; reject a both-off state so at least one Area remains. */
export function nextSeriesVisibility(
  current: SeriesVisibility,
  toggle: SeriesKey,
): SeriesVisibility {
  const next = { ...current, [toggle]: !current[toggle] }
  if (!next.mcp && !next.graph) return current
  return next
}
