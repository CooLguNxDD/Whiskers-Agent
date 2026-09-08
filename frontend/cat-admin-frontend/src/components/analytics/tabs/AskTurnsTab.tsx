import { useMemo, useState } from "react"
import { Chat, Spark, Clock, Filter, Search } from "@/components/shell/Icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table } from "@/components/Table/Table"
import type { Column } from "@/components/Table/useSort"
import type { AskTurn, AskTurnsResponse } from "@/api/analytics"
import { KpiStrip } from "@/components/analytics/KpiStrip"

const ASK_INTENTS = ["all", "focus_fish", "add_fish", "discover", "patch_blocks", "bake"] as const

function intentBadgeVariant(intent: string | null): "default" | "secondary" | "outline" {
  switch (intent) {
    case "focus_fish":
    case "add_fish":
    case "discover":
    case "patch_blocks":
    case "bake":
      return "default"
    default:
      return "secondary"
  }
}

const askColumns: Column<AskTurn>[] = [
  {
    key: "created_at",
    header: "When",
    sortable: true,
    render: (t) => (
      <span className="font-mono text-[11.5px] text-muted-foreground whitespace-nowrap">
        {t.created_at ? new Date(t.created_at).toLocaleString() : "—"}
      </span>
    ),
  },
  {
    key: "question",
    header: "Question",
    render: (t) => (
      <span className="truncate" title={t.question}>
        {t.question}
      </span>
    ),
  },
  {
    key: "intent",
    header: "Intent",
    render: (t) => (
      <Badge variant={intentBadgeVariant(t.intent)}>{t.intent || "unknown"}</Badge>
    ),
  },
  {
    key: "latency_ms",
    header: "Latency",
    align: "right",
    sortable: true,
    render: (t) => (
      <span className="font-mono">{t.latency_ms !== null ? `${t.latency_ms}ms` : "—"}</span>
    ),
  },
  {
    key: "session",
    header: "Session",
    render: (t) => (
      <span className="font-mono text-[11.5px] text-muted-foreground">
        {t.visitor_session_id || t.subject || "anonymous"}
      </span>
    ),
  },
]

/**
 * Ask-turns tab: 4 KPIs, intent chips (server-side via useAskTurns), client search.
 */
export function AskTurnsTab({
  askTurns,
  isAskTurnsError,
  intent,
  onIntentChange,
}: {
  askTurns: AskTurnsResponse | undefined
  isAskTurnsError: boolean
  intent: string
  onIntentChange: (intent: string) => void
}) {
  const [askSearch, setAskSearch] = useState("")

  const filteredAskTurns = useMemo(() => {
    if (!askTurns?.turns) return []
    if (!askSearch.trim()) return askTurns.turns
    const q = askSearch.toLowerCase().trim()
    return askTurns.turns.filter((t) => {
      const questionMatch = t.question?.toLowerCase().includes(q)
      const sessionMatch = (t.visitor_session_id || t.subject || "").toLowerCase().includes(q)
      return Boolean(questionMatch || sessionMatch)
    })
  }, [askTurns?.turns, askSearch])

  const stats = useMemo(() => {
    const turns = askTurns?.turns || []
    const total = turns.length
    const okCount = turns.filter((t) => t.ok).length
    const avgLatency =
      total > 0 ? Math.round(turns.reduce((acc, t) => acc + (t.latency_ms || 0), 0) / total) : 0
    return {
      total: askTurns?.count ?? total,
      successRate: total > 0 ? ((okCount / total) * 100).toFixed(1) : "100.0",
      avgLatency,
    }
  }, [askTurns])

  return (
    <div className="flex flex-col gap-4">
      <KpiStrip
        columns={4}
        items={[
          {
            key: "total",
            label: "Total Ask Turns",
            value: stats.total,
            sub: "recorded",
            delta: "visitor queries",
            deltaUp: true,
            icon: <Chat width="16" height="16" />,
            iconClass: "is-cyan",
          },
          {
            key: "ok",
            label: "Success Rate",
            value: stats.successRate,
            sub: "%",
            delta: "resolution",
            deltaUp: true,
            icon: <Spark width="16" height="16" />,
            iconClass: "is-neon",
          },
          {
            key: "lat",
            label: "Avg Latency",
            value: stats.avgLatency,
            sub: "ms",
            delta: "turn duration",
            icon: <Clock width="16" height="16" />,
          },
          {
            key: "showing",
            label: "Displaying",
            value: filteredAskTurns.length,
            sub: "filtered",
            delta: "filter view",
            icon: <Filter width="16" height="16" />,
            iconClass: "is-pink",
          },
        ]}
      />

      <Card>
        <CardHeader className="border-b flex-row items-center gap-2">
          <CardTitle>Visitor Ask Turns</CardTitle>
          <Badge variant="secondary">{filteredAskTurns.length} turns</Badge>
        </CardHeader>
        <CardContent className="space-y-3 px-0">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4">
            <div className="flex min-w-[260px] flex-1 items-center gap-2">
              <Search width="14" height="14" />
              <Input
                placeholder="Search questions or session IDs…"
                value={askSearch}
                onChange={(e) => setAskSearch(e.target.value)}
              />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {ASK_INTENTS.map((chip) => (
                <Button
                  key={chip}
                  type="button"
                  size="sm"
                  variant={intent === chip ? "default" : "outline"}
                  onClick={() => onIntentChange(chip)}
                >
                  {chip}
                </Button>
              ))}
            </div>
          </div>
          {isAskTurnsError ? (
            <div className="py-7 text-center text-destructive">
              Failed to load ask turns from server.
            </div>
          ) : (
            <Table
              columns={askColumns}
              rows={filteredAskTurns}
              rowKey={(t) => t.run_id}
              emptyMessage={
                askSearch || intent !== "all"
                  ? "No ask turns match your search filter."
                  : "No visitor ask turns recorded yet."
              }
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
