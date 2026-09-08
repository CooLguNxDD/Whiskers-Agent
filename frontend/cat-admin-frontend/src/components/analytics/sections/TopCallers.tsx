/**
 * Top callers (by model) usage bars.
 */
import type { TopModel } from "@/api/analytics"

export function TopCallers({ models }: { models: TopModel[] | undefined }) {
  return (
    <div className="ct-panel">
      <div className="ct-panel-head">
        <span className="ct-panel-title">Top callers</span>
        <span style={{ fontSize: 11, color: "var(--fg-muted)", fontFamily: "var(--font-mono)" }}>by model</span>
      </div>
      <div className="ct-panel-body">
        {models && models.length > 0 ? (
          models.map(({ name, calls, pct }) => (
            <div key={name} style={{ marginBottom: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, marginBottom: 4 }}>
                <span style={{ fontFamily: "var(--font-mono)" }}>{name}</span>
                <span style={{ color: "var(--fg-muted)", fontFamily: "var(--font-mono)" }}>
                  {calls.toLocaleString()} · {pct}%
                </span>
              </div>
              <div className="ct-usage-bar">
                <div className="ct-usage-fill is-neon" style={{ width: `${pct}%` }} />
              </div>
            </div>
          ))
        ) : (
          <div style={{ textAlign: "center", color: "var(--fg-muted)", padding: 24 }}>
            No callers recorded.
          </div>
        )}
      </div>
    </div>
  )
}
