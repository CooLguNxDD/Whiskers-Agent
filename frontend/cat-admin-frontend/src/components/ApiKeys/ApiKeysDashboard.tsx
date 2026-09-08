/**
 * ApiKeysDashboard — filter bar + keys table + pagination for the API Keys list view.
 */

import { Search } from "@/components/shell/Icons"
import { Table } from "@/components/Table/Table"
import { Pagination } from "@/components/Table/Pagination"
import type { Column } from "@/components/Table/useSort"
import type { ApiKey } from "@/api/apiKeys"

export type ApiKeyStatusFilter = "all" | "active" | "revoked"

interface ApiKeysDashboardProps {
  columns: Column<ApiKey>[]
  filtered: ApiKey[]
  totalCount: number
  query: string
  onQueryChange: (query: string) => void
  filter: ApiKeyStatusFilter
  onFilterChange: (filter: ApiKeyStatusFilter) => void
  page: number
  perPage: number
  onPageChange: (page: number) => void
  onPerPageChange: (perPage: number) => void
  onRowClick: (key: ApiKey) => void
}

const FILTERS: ApiKeyStatusFilter[] = ["all", "active", "revoked"]

/**
 * Main dashboard component for managing API keys.
 * Renders the API keys table, filter bar, and pagination.
 */
export function ApiKeysDashboard({
  columns,
  filtered,
  totalCount,
  query,
  onQueryChange,
  filter,
  onFilterChange,
  page,
  perPage,
  onPageChange,
  onPerPageChange,
  onRowClick,
}: ApiKeysDashboardProps) {
  return (
    <div className="ct-section">
      <div className="ct-filter-bar">
        <div className="ct-filter-search">
          <Search />
          <input
            placeholder="Filter by name, ID or prefix…"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
          />
        </div>
        {FILTERS.map((k) => (
          <button
            key={k}
            className={"ct-chip" + (filter === k ? " is-active" : "")}
            onClick={() => onFilterChange(k)}
          >
            {k}
            {filter === k && k !== "all" && <span className="ct-chip-x">×</span>}
          </button>
        ))}
      </div>

      <Table
        columns={columns}
        rows={filtered}
        page={page}
        perPage={perPage}
        rowKey={(k) => k.key_id}
        onRowClick={onRowClick}
        emptyMessage="no API keys match this filter"
      />
      <Pagination
        total={filtered.length}
        page={page}
        perPage={perPage}
        onPageChange={onPageChange}
        onPerPageChange={onPerPageChange}
        filteredNote={filter !== "all" ? { shown: filtered.length, ofTotal: totalCount } : undefined}
      />
    </div>
  )
}

export default ApiKeysDashboard
