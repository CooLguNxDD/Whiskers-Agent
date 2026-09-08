import { useMemo, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import AppShell from "@/components/shell/AppShell"
import { Activity, Cube, Chat, Shield } from "@/components/shell/Icons"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Badge } from "@/components/ui/badge"
import {
  useAnalyticsSummary,
  useLiveMetrics,
  useAskTurns,
  useInvalidateSummaryOnLiveChange,
} from "@/hooks/useAnalytics"
import { overlayErrors, overlayKpis } from "@/components/analytics/overlay"
import { RangeToggle, type AnalyticsRange } from "@/components/analytics/RangeToggle"
import { type AnalyticsTab } from "@/components/analytics/search"
import { OverviewTab } from "@/components/analytics/tabs/OverviewTab"
import { ToolsTrafficTab } from "@/components/analytics/tabs/ToolsTrafficTab"
import { AskTurnsTab } from "@/components/analytics/tabs/AskTurnsTab"
import { ErrorsHealthTab } from "@/components/analytics/tabs/ErrorsHealthTab"

export type { AnalyticsTab } from "@/components/analytics/search"
export { ANALYTICS_TABS } from "@/components/analytics/search"

/**
 * Analytics dashboard controller — queries, WS overlay, URL-owned tab/range,
 * shadcn Tabs. AppShell + ct-page-head chrome match Config/ApiKeys.
 */
export function AnalyticsPage({
  tab = "overview",
  range = "24h",
}: {
  tab?: AnalyticsTab
  range?: AnalyticsRange
}) {
  const navigate = useNavigate()
  const [askIntent, setAskIntent] = useState("all")

  const { data: summary } = useAnalyticsSummary(range)
  const { metrics: liveMetrics, status: wsStatus } = useLiveMetrics()
  const intentArg = askIntent === "all" ? undefined : askIntent
  const { data: askTurns, isError: isAskTurnsError } = useAskTurns(20, intentArg)

  useInvalidateSummaryOnLiveChange(range, liveMetrics)

  const kpis = useMemo(() => overlayKpis(summary, liveMetrics), [summary, liveMetrics])
  const errors = useMemo(() => overlayErrors(summary, liveMetrics), [summary, liveMetrics])

  const setSearch = (patch: { tab?: AnalyticsTab; range?: AnalyticsRange }) => {
    void navigate({
      to: "/analytics",
      search: { tab: patch.tab ?? tab, range: patch.range ?? range },
      replace: true,
    } as never)
  }

  return (
    <AppShell active="analytics">
      <div className="ct-page-head">
        <div>
          <div className="ct-page-title">
            Analytics
            <span className="ct-eyebrow">
              / traffic · errors · latency
              <span
                className={`ct-status-indicator ${wsStatus}`}
                style={{ marginLeft: 8, fontSize: 10, display: "inline-flex", alignItems: "center", gap: 4 }}
              >
                <span
                  className="dot"
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background:
                      wsStatus === "connected"
                        ? "var(--neon)"
                        : wsStatus === "connecting"
                          ? "var(--amber)"
                          : "var(--fg-muted)",
                    display: "inline-block",
                  }}
                />
                {wsStatus}
              </span>
            </span>
          </div>
          <div className="ct-page-sub">Aggregate signals across every MCP plugin & layer-2 connection.</div>
        </div>
        <RangeToggle range={range} onChange={(next) => setSearch({ range: next })} />
      </div>

      <Tabs value={tab} onValueChange={(value) => setSearch({ tab: value as AnalyticsTab })}>
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="overview" onClick={() => setSearch({ tab: "overview" })}>
            <Activity width="14" height="14" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="tools" onClick={() => setSearch({ tab: "tools" })}>
            <Cube width="14" height="14" />
            Tools & Traffic
            {summary?.top_tools && summary.top_tools.length > 0 && (
              <Badge variant="secondary">{summary.top_tools.length}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="ask_turns" onClick={() => setSearch({ tab: "ask_turns" })}>
            <Chat width="14" height="14" />
            Ask Turns
            {askTurns?.count !== undefined && askTurns.count > 0 && (
              <Badge variant="secondary">{askTurns.count}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="errors" onClick={() => setSearch({ tab: "errors" })}>
            <Shield width="14" height="14" />
            Errors & Health
            <Badge variant={errors.length > 0 ? "destructive" : "secondary"}>{errors.length}</Badge>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewTab
            range={range}
            summary={summary}
            kpis={kpis}
            errors={errors}
            askTurnCount={askTurns?.count ?? 0}
            wsStatus={wsStatus}
            onSelectTab={(next) => setSearch({ tab: next })}
          />
        </TabsContent>
        <TabsContent value="tools">
          <ToolsTrafficTab range={range} summary={summary} />
        </TabsContent>
        <TabsContent value="ask_turns">
          <AskTurnsTab
            askTurns={askTurns}
            isAskTurnsError={isAskTurnsError}
            intent={askIntent}
            onIntentChange={setAskIntent}
          />
        </TabsContent>
        <TabsContent value="errors">
          <ErrorsHealthTab
            range={range}
            errors={errors}
            successRate={kpis.successRate}
            p99={kpis.p99}
          />
        </TabsContent>
      </Tabs>
    </AppShell>
  )
}
