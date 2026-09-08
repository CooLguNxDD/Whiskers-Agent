import { useMemo, useState } from "react"
import { Shield, Spark, Clock, Search } from "@/components/shell/Icons"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table } from "@/components/Table/Table"
import type { Column } from "@/components/Table/useSort"
import type { RecentError } from "@/api/analytics"
import { KpiStrip } from "@/components/analytics/KpiStrip"

const errorColumns: Column<RecentError>[] = [
  {
    key: "code",
    header: "Code",
    width: "90px",
    render: (e) => <Badge variant="destructive">{e.code}</Badge>,
  },
  {
    key: "src",
    header: "Source",
    sortable: true,
    render: (e) => <span className="font-mono font-semibold">{e.src}</span>,
  },
  {
    key: "msg",
    header: "Message",
    render: (e) => <span className="text-muted-foreground">{e.msg}</span>,
  },
  {
    key: "when",
    header: "When",
    align: "right",
    width: "120px",
    render: (e) => <span className="font-mono text-[11.5px] text-muted-foreground">{e.when}</span>,
  },
]

/**
 * Errors tab: summary KPIs plus a client-filtered error stream table.
 */
export function ErrorsHealthTab({
  range,
  errors,
  successRate,
  p99,
}: {
  range: string
  errors: RecentError[]
  successRate: number
  p99: number
}) {
  const [errorSearch, setErrorSearch] = useState("")

  const filteredErrors = useMemo(() => {
    if (!errorSearch.trim()) return errors
    const q = errorSearch.toLowerCase().trim()
    return errors.filter(
      (e) => e.src.toLowerCase().includes(q) || e.msg.toLowerCase().includes(q) || e.code.includes(q),
    )
  }, [errors, errorSearch])

  return (
    <div className="flex flex-col gap-4">
      <KpiStrip
        columns={3}
        items={[
          {
            key: "err",
            label: "Total Errors",
            value: errors.length,
            sub: "in range",
            delta: `${range} interval`,
            icon: <Shield width="16" height="16" />,
            iconClass: errors.length > 0 ? "is-err" : "is-neon",
          },
          {
            key: "avail",
            label: "System Availability",
            value: successRate.toFixed(2),
            sub: "%",
            delta: "health",
            deltaUp: true,
            icon: <Spark width="16" height="16" />,
            iconClass: "is-neon",
          },
          {
            key: "p99",
            label: "Tail Latency",
            value: p99,
            sub: "ms",
            delta: "p99 tail",
            icon: <Clock width="16" height="16" />,
            iconClass: "is-cyan",
          },
        ]}
      />

      <Card>
        <CardHeader className="border-b flex-row items-center gap-2">
          <CardTitle>Error Diagnostics & Trace Log</CardTitle>
          <Badge variant={filteredErrors.length > 0 ? "destructive" : "secondary"}>
            {filteredErrors.length} recorded
          </Badge>
        </CardHeader>
        <CardContent className="space-y-3 px-0">
          <div className="flex items-center gap-2 px-4">
            <Search width="14" height="14" />
            <Input
              placeholder="Filter errors by plugin, code or error message…"
              value={errorSearch}
              onChange={(e) => setErrorSearch(e.target.value)}
            />
          </div>
          <Table
            columns={errorColumns}
            rows={filteredErrors}
              rowKey={(e) => `${e.src}-${e.code}-${e.msg}-${e.when}`}
            emptyMessage={
              errorSearch
                ? `No errors matching "${errorSearch}"`
                : `All plugins and services operated with 100% success in the ${range} window.`
            }
          />
        </CardContent>
      </Card>
    </div>
  )
}
