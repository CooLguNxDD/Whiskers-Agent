import { useMemo } from "react"
import { Chevron } from "@/components/shell/Icons"

interface PaginationProps {
  total: number
  page: number
  perPage: number
  onPageChange: (page: number) => void
  onPerPageChange: (perPage: number) => void
  filteredNote?: {
    shown: number
    ofTotal: number
  }
}

const PER_PAGE_OPTIONS = [10, 25, 50]

/**
 * A controlled pagination component for data tables.
 */
export function Pagination({
  total,
  page,
  perPage,
  onPageChange,
  onPerPageChange,
  filteredNote,
}: PaginationProps) {
  const pageCount = useMemo(() => Math.ceil(total / perPage), [total, perPage])
  const safePage = useMemo(() => Math.max(1, Math.min(page, pageCount)), [page, pageCount])

  const start = useMemo(() => (safePage - 1) * perPage, [safePage, perPage])

  const pageList = useMemo(() => {
    if (pageCount <= 7) {
      return Array.from({ length: pageCount }, (_, i) => i + 1)
    }

    const pages: (number | string)[] = []
    pages.push(1)

    const startPage = Math.max(2, safePage - 1)
    const endPage = Math.min(pageCount - 1, safePage + 1)

    if (startPage > 2) {
      pages.push("…")
    }

    for (let i = startPage; i <= endPage; i++) {
      pages.push(i)
    }

    if (endPage < pageCount - 1) {
      pages.push("…")
    }

    pages.push(pageCount)
    return pages
  }, [safePage, pageCount])

  if (total === 0) return null

  return (
    <div className="ct-pagination">
      <div>
        showing{" "}
        <span style={{ color: "var(--fg)" }}>
          {total === 0 ? 0 : start + 1}–{Math.min(start + perPage, total)}
        </span>{" "}
        of <span style={{ color: "var(--fg)" }}>{total}</span>
        {filteredNote && (
          <> · filtered from {filteredNote.ofTotal}</>
        )}
      </div>

      <div className="ct-page-pages">
        <button
          className="ct-page-btn"
          disabled={safePage === 1}
          onClick={() => onPageChange(Math.max(1, safePage - 1))}
        >
          ← prev
        </button>
        {pageList.map((n, i) =>
          n === "…" ? (
            <span key={"e" + i} className="ct-page-ellip">
              …
            </span>
          ) : (
            <button
              key={n}
              className={"ct-page-btn" + (n === safePage ? " is-active" : "")}
              aria-current={n === safePage ? "page" : undefined}
              onClick={() => onPageChange(n as number)}
            >
              {n}
            </button>
          )
        )}
        <button
          className="ct-page-btn"
          disabled={safePage === pageCount}
          onClick={() => onPageChange(Math.min(pageCount, safePage + 1))}
        >
          next <Chevron width="11" height="11" />
        </button>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span>rows</span>
        {PER_PAGE_OPTIONS.map((n) => (
          <button
            key={n}
            className={"ct-page-btn" + (perPage === n ? " is-active" : "")}
            onClick={() => {
              onPerPageChange(n)
            }}
          >
            {n}
          </button>
        ))}
      </div>
    </div>
  )
}
