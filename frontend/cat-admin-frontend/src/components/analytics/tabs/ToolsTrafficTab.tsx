import { useCallback, useMemo, useState } from "react"
import { Download, Search } from "@/components/shell/Icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table } from "@/components/Table/Table"
import type { Column } from "@/components/Table/useSort"
import type { AnalyticsSummaryResponse, TopTool } from "@/api/analytics"
import { PluginStackedChart } from "@/components/analytics/charts/PluginStackedChart"
import { TinySpark } from "@/components/analytics/charts/TinySpark"

const toolColumns: Column<TopTool>[] = [
  {
    key: "name",
    header: "Tool",
    sortable: true,
    render: (row) => <span className="tool-name font-mono">{row.name}</span>,
  },
  {
    key: "calls",
    header: "Calls",
    align: "right",
    sortable: true,
    defaultDir: "desc",
    render: (row) => <span className="font-mono">{row.calls.toLocaleString()}</span>,
  },
  {
    key: "trend",
    header: "Trend",
    align: "right",
    render: (row) => <TinySpark values={row.trend} />,
  },
  {
    key: "p99",
    header: "p99",
    align: "right",
    render: (row) => <span className="font-mono">{row.p99}</span>,
  },
]

/**
 * Tools tab: stacked plugin chart, searchable tools table, CSV export, top callers.
 */
export function ToolsTrafficTab({
  range,
  summary,
}: {
  range: string
  summary: AnalyticsSummaryResponse | undefined
}) {
  const [toolSearch, setToolSearch] = useState("")

  const filteredTools = useMemo(() => {
    if (!summary?.top_tools) return []
    if (!toolSearch.trim()) return summary.top_tools
    const q = toolSearch.toLowerCase().trim()
    return summary.top_tools.filter((t) => t.name.toLowerCase().includes(q))
  }, [summary?.top_tools, toolSearch])

  const handleExportCSV = useCallback(() => {
    if (!summary?.top_tools) return
    const headers = ["Tool", "Calls", "p99"]
    const rows = summary.top_tools.map((t) => [t.name, t.calls, t.p99])
    const csvContent = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n")
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = `top_tools_${range}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }, [summary, range])

  return (
    <div className="flex flex-col gap-4">
      <PluginStackedChart series={summary?.series} range={range} />

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="border-b flex-row items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <CardTitle>Tool Operations</CardTitle>
              <Badge variant="secondary">
                {filteredTools.length} of {summary?.top_tools?.length ?? 0}
              </Badge>
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={handleExportCSV}>
              <Download width="12" height="12" /> Export CSV
            </Button>
          </CardHeader>
          <CardContent className="space-y-3 px-0">
            <div className="flex items-center gap-2 px-4">
              <Search width="14" height="14" />
              <Input
                placeholder="Search tools by name…"
                value={toolSearch}
                onChange={(e) => setToolSearch(e.target.value)}
              />
            </div>
            <Table
              columns={toolColumns}
              rows={filteredTools}
              rowKey={(r) => r.name}
              emptyMessage={
                toolSearch ? `No tools matching "${toolSearch}"` : "No tools active in this range."
              }
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b flex-row items-center justify-between">
            <CardTitle>Top Callers</CardTitle>
            <span className="font-mono text-[11px] text-muted-foreground">by LLM model</span>
          </CardHeader>
          <CardContent>
            {summary?.top_models && summary.top_models.length > 0 ? (
              summary.top_models.map(({ name, calls, pct }) => (
                <div key={name} className="mb-3.5">
                  <div className="mb-1.5 flex justify-between text-xs">
                    <span className="font-mono font-medium">{name}</span>
                    <span className="font-mono text-muted-foreground">
                      {calls.toLocaleString()} calls · {pct}%
                    </span>
                  </div>
                  <div className="ct-usage-bar">
                    <div className="ct-usage-fill is-neon" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              ))
            ) : (
              <div className="py-8 text-center text-muted-foreground">
                No callers recorded in this range.
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
