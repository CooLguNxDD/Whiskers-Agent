/**
 * LogsTab Component
 *
 * Paginated, status-filterable tool-call history for a plugin, backed by
 * GET /api/plugins/{plugin_id}/logs (tool_call_events table).
 */
import { useState, type FC } from "react"
import { cn } from "@/lib/utils"
import { usePluginLogsQuery } from "@/hooks/usePlugins"
import { Table } from "@/components/Table/Table"
import { Pagination } from "@/components/Table/Pagination"
import { LogStatusFilters } from "./components/LogStatusFilters"
import type { Column } from "@/components/Table/useSort"
import type { PluginLogEntry, PluginLogStatusFilter } from "@/api/plugins"

export interface LogsTabProps {
  pluginId: string
  className?: string
}

function formatWhen(iso: string | null): string {
  if (!iso) return "—"
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return "—"
  const diffMs = Date.now() - date.getTime()
  const diffS = Math.floor(diffMs / 1000)
  if (diffS < 60) return "just now"
  if (diffS < 3600) return `${Math.floor(diffS / 60)}m ago`
  if (diffS < 86400) return `${Math.floor(diffS / 3600)}h ago`
  return `${Math.floor(diffS / 86400)}d ago`
}

const columns: Column<PluginLogEntry>[] = [
  {
    key: "ok",
    header: "Status",
    width: "90px",
    render: (row) => (
      <span className={"ct-pill " + (row.ok ? "is-ok" : "is-err")}>
        <span className={"ct-dot " + (row.ok ? "is-ok" : "is-err")} />
        {row.ok ? "ok" : "error"}
      </span>
    ),
  },
  {
    key: "tool_name",
    header: "Tool",
    sortable: true,
    render: (row) => (
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{row.tool_name}</span>
    ),
  },
  {
    key: "detail",
    header: "Detail",
    render: (row) =>
      row.ok ? (
        <span style={{ color: "var(--fg-muted)", fontSize: 12 }}>{row.subject ?? "—"}</span>
      ) : (
        <span style={{ color: "var(--danger)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
          {row.status_code ?? ""} {row.error_type ?? "error"}
        </span>
      ),
  },
  {
    key: "model",
    header: "Model",
    width: "160px",
    render: (row) => (
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--fg-muted)" }}>
        {row.model ?? "—"}
      </span>
    ),
  },
  {
    key: "latency_ms",
    header: "Latency",
    width: "90px",
    align: "right",
    sortable: true,
    render: (row) => (
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{row.latency_ms}ms</span>
    ),
  },
  {
    key: "created_at",
    header: "When",
    width: "100px",
    align: "right",
    sortable: true,
    render: (row) => (
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--fg-muted)" }}>
        {formatWhen(row.created_at)}
      </span>
    ),
  },
]

const LogsTab: FC<LogsTabProps> = ({ pluginId, className }) => {
  const [status, setStatus] = useState<PluginLogStatusFilter>("all")
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)

  const { data, isPending, isError } = usePluginLogsQuery(pluginId, page, perPage, status)

  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <LogStatusFilters
          status={status}
          onStatusChange={(newStatus) => {
            setStatus(newStatus)
            setPage(1)
          }}
        />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg-subtle)" }}>
          {total} call{total === 1 ? "" : "s"}
        </span>
      </div>

      {isError && (
        <div className="ct-panel">
          <div className="ct-panel-body" style={{ color: "var(--danger)", fontSize: 12 }}>
            Failed to load logs.
          </div>
        </div>
      )}

      <Table
        columns={columns}
        rows={items}
        rowKey={(r) => String(r.id)}
        emptyMessage={isPending ? "loading…" : "no tool calls recorded for this plugin yet"}
      />

      <Pagination
        total={total}
        page={page}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={(n) => {
          setPerPage(n)
          setPage(1)
        }}
      />
    </div>
  )
}

/**
 * LogsTab Component.
 * Renders the UI and handles state for the LogsTab feature.
 */
export default LogsTab
