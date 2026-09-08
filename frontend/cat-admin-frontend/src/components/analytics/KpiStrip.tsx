import type { ReactNode } from "react"

export type KpiItem = {
  key: string
  label: string
  value: ReactNode
  sub?: ReactNode
  delta?: string
  deltaUp?: boolean
  icon: ReactNode
  iconClass?: string
}

/**
 * ct-stat KPI strip — 5 cards on overview, 4 on ask turns, 3 on errors.
 * Shell chrome stays ct-*; tab panels themselves use shadcn.
 */
export function KpiStrip({ items, columns }: { items: KpiItem[]; columns: 3 | 4 | 5 }) {
  return (
    <div className={`ct-stats is-${columns}`}>
      {items.map((item) => (
        <div className="ct-stat" key={item.key}>
          <div className="ct-stat-head">
            <div className={`ct-stat-ico ${item.iconClass ?? ""}`}>{item.icon}</div>
            {item.delta != null && (
              <div className={`ct-stat-delta${item.deltaUp ? " is-up" : ""}`}>{item.delta}</div>
            )}
          </div>
          <div className="ct-stat-label">{item.label}</div>
          <div className="ct-stat-value">
            {item.value}
            {item.sub != null && <span className="ct-stat-sub">{item.sub}</span>}
          </div>
        </div>
      ))}
    </div>
  )
}
