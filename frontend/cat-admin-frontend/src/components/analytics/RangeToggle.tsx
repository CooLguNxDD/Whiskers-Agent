import { Button } from "@/components/ui/button"

export const ANALYTICS_RANGES = ["1h", "24h", "7d", "30d"] as const
export type AnalyticsRange = (typeof ANALYTICS_RANGES)[number]

export function isAnalyticsRange(value: unknown): value is AnalyticsRange {
  return typeof value === "string" && (ANALYTICS_RANGES as readonly string[]).includes(value)
}

/**
 * 1h / 24h / 7d / 30d range toggle — URL-owned; parent writes search params.
 */
export function RangeToggle({
  range,
  onChange,
}: {
  range: AnalyticsRange
  onChange: (range: AnalyticsRange) => void
}) {
  return (
    <div className="flex items-center gap-2" role="group" aria-label="Time range">
      {ANALYTICS_RANGES.map((r) => (
        <Button
          key={r}
          type="button"
          size="sm"
          variant={range === r ? "default" : "ghost"}
          aria-pressed={range === r}
          onClick={() => onChange(r)}
        >
          {r}
        </Button>
      ))}
    </div>
  )
}
