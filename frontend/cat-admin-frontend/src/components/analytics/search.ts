export const ANALYTICS_TABS = ["overview", "tools", "ask_turns", "errors"] as const
export type AnalyticsTab = (typeof ANALYTICS_TABS)[number]

export function isAnalyticsTab(value: unknown): value is AnalyticsTab {
  return typeof value === "string" && (ANALYTICS_TABS as readonly string[]).includes(value)
}

export type AnalyticsSearch = {
  tab: AnalyticsTab
  range: import("./RangeToggle").AnalyticsRange
}
