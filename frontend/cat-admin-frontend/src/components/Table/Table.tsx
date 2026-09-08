import React from "react"
import { useSort, type Column } from "./useSort"
import { ChevronUp, ChevronDown } from "lucide-react"

interface TableProps<T> {
  columns: Column<T>[]
  rows: T[]
  page?: number
  perPage?: number
  rowKey: (item: T) => string
  onRowClick?: (item: T) => void
  emptyMessage?: string
}

/**
 * A generic, customizable data table component.
 */
export function Table<T>({
  columns,
  rows,
  page,
  perPage,
  rowKey,
  onRowClick,
  emptyMessage = "no items match this filter",
}: TableProps<T>) {
  const { sortedItems, sortKey, sortDir, handleSort } = useSort(rows, columns)

  const paginatedRows = React.useMemo(() => {
    if (page === undefined || perPage === undefined) return sortedItems
    const start = (page - 1) * perPage
    return sortedItems.slice(start, start + perPage)
  }, [sortedItems, page, perPage])

  const gridTemplateColumns = React.useMemo(() => {
    return columns.map((c) => c.width || "1fr").join(" ")
  }, [columns])

  return (
    <div
      className="w-full overflow-x-auto border border-[var(--hairline)] rounded-xl bg-card ct-table-custom"
      role="table"
    >
      <div className="min-w-[700px]">
        {/* Table Header */}
        <div role="rowgroup">
          <div
            role="row"
            className="border-b border-[var(--hairline)] h-10 px-4 font-mono text-[10px] uppercase tracking-wider text-muted-foreground bg-muted/20"
            style={{
              display: "grid",
              gridTemplateColumns,
              alignItems: "center",
            }}
          >
            {columns.map((col) => {
              const isSorted = sortKey === col.key
              const alignStyle =
                col.align === "right"
                  ? "text-right justify-end"
                  : col.align === "center"
                  ? "text-center justify-center"
                  : "text-left justify-start"

              return (
                <div
                  key={col.key}
                  role="columnheader"
                  className={`flex items-center gap-1 py-2 font-semibold ${alignStyle} ${
                    col.sortable
                      ? "cursor-pointer select-none hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:rounded"
                      : ""
                  }`}
                  tabIndex={col.sortable ? 0 : undefined}
                  onClick={(e) => {
                    if (col.sortable) {
                      e.stopPropagation()
                      handleSort(col.key)
                    }
                  }}
                  onKeyDown={(e) => {
                    if (col.sortable && (e.key === "Enter" || e.key === " ")) {
                      e.preventDefault()
                      e.stopPropagation()
                      handleSort(col.key)
                    }
                  }}
                >
                  <span>{col.header}</span>
                  {col.sortable && isSorted && sortDir && (
                    <span className="inline-flex">
                      {sortDir === "asc" ? (
                        <ChevronUp className="size-3 text-foreground" />
                      ) : (
                        <ChevronDown className="size-3 text-foreground" />
                      )}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Table Body */}
        <div className="divide-y divide-[var(--hairline)]" role="rowgroup">
          {paginatedRows.length === 0 ? (
            <div className="py-12 text-center text-xs text-muted-foreground font-mono uppercase tracking-wider">
              {emptyMessage}
            </div>
          ) : (
            paginatedRows.map((row) => (
              <div
                key={rowKey(row)}
                role="row"
                onClick={() => onRowClick?.(row)}
                onKeyDown={(e) => {
                  // Nested cell controls handle their own keys.
                  if (e.target !== e.currentTarget) return
                  if (onRowClick && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault()
                    onRowClick(row)
                  }
                }}
                tabIndex={onRowClick ? 0 : undefined}
                className={`px-4 h-12 hover:bg-[color-mix(in_oklch,var(--fg)_4%,transparent)] transition-colors text-xs text-foreground ${
                  onRowClick ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring -outline-offset-2" : ""
                }`}
                style={{
                  display: "grid",
                  gridTemplateColumns,
                  alignItems: "center",
                }}
              >
                {columns.map((col) => {
                  const alignStyle =
                    col.align === "right"
                      ? "text-right justify-end"
                      : col.align === "center"
                      ? "text-center justify-center"
                      : "text-left justify-start"

                  const content = col.render
                    ? col.render(row)
                    : (row as Record<string, unknown>)[col.key] as React.ReactNode

                  return (
                    <div
                      key={col.key}
                      role="cell"
                      className={`flex items-center w-full ${alignStyle} ${
                        col.truncate !== false ? "truncate" : ""
                      }`}
                    >
                      {content}
                    </div>
                  )
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
