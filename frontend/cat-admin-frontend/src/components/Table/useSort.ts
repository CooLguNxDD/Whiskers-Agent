import { useState, useMemo } from "react"

export interface Column<T> {
  key: string
  header: React.ReactNode
  width?: string
  sortable?: boolean
  defaultDir?: "asc" | "desc"
  sortValue?: (item: T) => unknown
  truncate?: boolean
  align?: "left" | "center" | "right"
  render?: (item: T) => React.ReactNode
}

/**
  * A hook to sort items based on column definitions and active sorting state.
  */
export function useSort<T>(items: T[], columns: Column<T>[]) {
  // Find the column that should be sorted by default
  const defaultSortColumn = columns.find((col) => col.sortable && col.defaultDir)

  const [sortKey, setSortKey] = useState<string | null>(
    defaultSortColumn ? defaultSortColumn.key : null
  )
  const [sortDir, setSortDir] = useState<"asc" | "desc" | null>(
    defaultSortColumn ? (defaultSortColumn.defaultDir ?? "asc") : null
  )

  const handleSort = (key: string) => {
    const col = columns.find((c) => c.key === key)
    if (!col || !col.sortable) return

    if (sortKey === key) {
      if (sortDir === "asc") {
        setSortDir("desc")
      } else if (sortDir === "desc") {
        // Reset sort
        setSortKey(null)
        setSortDir(null)
      } else {
        setSortDir("asc")
      }
    } else {
      setSortKey(key)
      setSortDir(col.defaultDir ?? "asc")
    }
  }

  const sortedItems = useMemo(() => {
    if (!sortKey || !sortDir) return items

    const col = columns.find((c) => c.key === sortKey)
    if (!col || !col.sortable) return items

    const sortValueFn = col.sortValue || ((item: T) => (item as Record<string, unknown>)[sortKey])

    return [...items].sort((a, b) => {
      const valA = sortValueFn(a)
      const valB = sortValueFn(b)

      if (valA === valB) return 0
      if (valA == null) return 1
      if (valB == null) return -1

      const factor = sortDir === "asc" ? 1 : -1
      if (typeof valA === "string" && typeof valB === "string") {
        return valA.localeCompare(valB) * factor
      }
      return ((valA as number) < (valB as number) ? -1 : 1) * factor
    })
  }, [items, columns, sortKey, sortDir])

  return {
    sortedItems,
    sortKey,
    sortDir,
    handleSort,
  }
}
