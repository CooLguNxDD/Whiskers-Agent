import { createFileRoute } from "@tanstack/react-router"
import { AnalyticsPage } from "@/components/analytics/AnalyticsPage"
import { isAnalyticsTab } from "@/components/analytics/search"
import { isAnalyticsRange } from "@/components/analytics/RangeToggle"

/**
 * `/analytics` route: `?tab=` + `?range=` deep-link the dashboard (shareable).
 */
export const Route = createFileRoute("/analytics")({
  validateSearch: (s: Record<string, unknown>) => ({
    tab: isAnalyticsTab(s.tab) ? s.tab : "overview",
    range: isAnalyticsRange(s.range) ? s.range : "24h",
  }),
  component: function AnalyticsRoute() {
    const search = Route.useSearch()
    return <AnalyticsPage tab={search.tab} range={search.range} />
  },
})
