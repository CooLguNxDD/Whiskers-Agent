/**
 * Fish-tank ask-turn audit table.
 */
import type { AskTurnsResponse } from "@/api/analytics"

export function AskTurnsTable({
  askTurns,
  isError,
}: {
  askTurns: AskTurnsResponse | undefined
  isError: boolean
}) {
  return (
    <div className="ct-panel" style={{ marginTop: 16 }}>
      <div className="ct-panel-head">
        <span className="ct-panel-title">Fish tank ask turns</span>
        <span style={{ fontSize: 11, color: "var(--fg-muted)", fontFamily: "var(--font-mono)" }}>
          {askTurns?.count ?? 0} recent
        </span>
      </div>
      <table className="ct-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Question</th>
            <th>Intent</th>
            <th style={{ textAlign: "right" }}>Latency</th>
            <th>Session</th>
          </tr>
        </thead>
        <tbody>
          {isError ? (
            <tr>
              <td colSpan={5} style={{ textAlign: "center", color: "var(--danger, var(--fg-muted))", padding: "24px 0" }}>
                Failed to load ask turns.
              </td>
            </tr>
          ) : askTurns?.turns && askTurns.turns.length > 0 ? (
            askTurns.turns.map((t) => (
              <tr key={t.run_id}>
                <td className="mono" style={{ fontSize: 11.5, color: "var(--fg-subtle)" }}>
                  {t.created_at ? new Date(t.created_at).toLocaleString() : "—"}
                </td>
                <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.question}
                </td>
                <td>
                  <span className={`ct-pill ${t.ok ? "is-ok" : "is-err"}`}>
                    {t.intent || "unknown"}
                  </span>
                </td>
                <td className="mono" style={{ textAlign: "right" }}>
                  {t.latency_ms !== null ? `${t.latency_ms}ms` : "—"}
                </td>
                <td className="mono" style={{ fontSize: 11.5, color: "var(--fg-muted)" }}>
                  {t.visitor_session_id || t.subject || "anonymous"}
                </td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={5} style={{ textAlign: "center", color: "var(--fg-muted)", padding: "24px 0" }}>
                No ask turns recorded yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
