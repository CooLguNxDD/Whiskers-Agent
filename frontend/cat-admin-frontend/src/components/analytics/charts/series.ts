/**
 * Pure adapters: analytics API series → Recharts rows + axis labels.
 */
import { BUCKET_COUNT } from "../analyticsConfig"

export const EMPTY_SERIES: number[] = Array(BUCKET_COUNT).fill(0)

export interface TrafficRow {
  i: number
  label: string
  mcp?: number
  graph?: number
  core?: number
  extensions?: number
  other?: number
}

/** Pad/trim a bucket series to BUCKET_COUNT; missing → zeros. */
export function padSeries(values: number[] | undefined, length = BUCKET_COUNT): number[] {
  if (!values?.length) return Array(length).fill(0)
  if (values.length === length) return values
  const out = values.slice(0, length)
  while (out.length < length) out.push(0)
  return out
}

/** X-axis label for bucket i given the selected range. */
export function bucketLabel(range: string, i: number): string {
  if (range === "24h") return `${String(i).padStart(2, "0")}:00`
  if (range === "1h") return `${i * 2.5}m`
  return `B${i}`
}

/** Dual-axis mcp vs graph rows (independent y-scales; not stacked). */
export function toMcpGraphRows(
  series: { mcp?: number[]; graph?: number[] } | undefined,
  range: string,
): TrafficRow[] {
  const mcp = padSeries(series?.mcp)
  const graph = padSeries(series?.graph)
  return mcp.map((v, i) => ({
    i,
    label: bucketLabel(range, i),
    mcp: v,
    graph: graph[i] ?? 0,
  }))
}

/** Stacked plugin-heuristic rows (core + extensions + other). */
export function toStackedRows(
  series: { core?: number[]; extensions?: number[]; other?: number[] } | undefined,
  range: string,
): TrafficRow[] {
  const core = padSeries(series?.core)
  const extensions = padSeries(series?.extensions)
  const other = padSeries(series?.other)
  return core.map((v, i) => ({
    i,
    label: bucketLabel(range, i),
    core: v,
    extensions: extensions[i] ?? 0,
    other: other[i] ?? 0,
  }))
}

/** Sparse tick labels matching the old 0/6/12/18/last cadence. */
export function sparseTickLabels(rows: TrafficRow[]): string[] {
  if (!rows.length) return []
  const last = rows.length - 1
  const idxs = [0, 6, 12, 18, last].filter((i) => i >= 0 && i <= last)
  return [...new Set(idxs)].map((i) => rows[i].label)
}
