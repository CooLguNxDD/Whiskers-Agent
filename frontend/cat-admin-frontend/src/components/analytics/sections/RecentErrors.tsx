/**
 * Recent error list for the analytics dashboard.
 */
import type { RecentError } from "@/api/analytics"

export function RecentErrors({ errors, range }: { errors: RecentError[]; range: string }) {
  return (
    <div className="ct-panel">
      <div className="ct-panel-head">
        <span className="ct-panel-title">Recent errors</span>
        <span className="ct-pill is-err">{errors.length} · {range}</span>
      </div>
      <div className="ct-panel-body" style={{ padding: 0 }}>
        {errors.length > 0 ? (
          errors.map(({ code, src, msg, when }, i) => (
            <div
              key={`${src}-${code}-${i}`}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr auto",
                gap: 12,
                alignItems: "center",
                padding: "11px 18px",
                borderBottom: i < errors.length - 1 ? "1px solid var(--hairline)" : "none",
              }}
            >
              <span className="ct-pill is-err" style={{ minWidth: 48, justifyContent: "center" }}>{code}</span>
              <div>
                <div style={{ fontSize: 13, fontFamily: "var(--font-mono)", color: "var(--fg)" }}>{src}</div>
                <div style={{ fontSize: 11.5, color: "var(--fg-muted)", marginTop: 2 }}>{msg}</div>
              </div>
              <span style={{ fontSize: 11.5, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>{when}</span>
            </div>
          ))
        ) : (
          <div style={{ textAlign: "center", color: "var(--fg-muted)", padding: 24 }}>
            No errors recorded in this range.
          </div>
        )}
      </div>
    </div>
  )
}
