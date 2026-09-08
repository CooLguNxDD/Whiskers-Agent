import type { FC } from "react"
import type { PluginLogStatusFilter } from "@/api/plugins"

export interface LogStatusFiltersProps {
  status: PluginLogStatusFilter
  onStatusChange: (status: PluginLogStatusFilter) => void
}

const STATUS_FILTERS: { id: PluginLogStatusFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "ok", label: "OK" },
  { id: "error", label: "Errors" },
]

/**
 * A filter tab component for plugin logs.
 */
export const LogStatusFilters: FC<LogStatusFiltersProps> = ({ status, onStatusChange }) => {
  return (
    <div className="ct-tabs" style={{ border: "none" }}>
      {STATUS_FILTERS.map((f) => (
        <button
          key={f.id}
          className={"ct-tab" + (status === f.id ? " is-active" : "")}
          onClick={() => onStatusChange(f.id)}
        >
          {f.label}
        </button>
      ))}
    </div>
  )
}
