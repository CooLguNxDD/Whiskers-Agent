/**
 * Top tools table with CSV export and CSS sparklines.
 */
import { useCallback } from "react"
import type { TopTool } from "@/api/analytics"
import { TinySpark } from "../charts/TinySpark"

export function TopToolsTable({ tools, range }: { tools: TopTool[] | undefined; range: string }) {
  const handleExportCSV = useCallback(() => {
    if (!tools) return
    const headers = ["Tool", "Calls", "p99"]
    const rows = tools.map((t) => [t.name, t.calls, t.p99])
    const csvContent = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n")

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = `top_tools_${range}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }, [tools, range])

  return (
    <div className="ct-panel">
      <div className="ct-panel-head">
        <span className="ct-panel-title">Top tools · {range}</span>
        <button className="ct-btn-ghost" style={{ padding: "4px 9px", fontSize: 11 }} onClick={handleExportCSV}>Export CSV</button>
      </div>
      <table className="ct-table">
        <thead>
          <tr>
            <th>Tool</th>
            <th style={{ textAlign: "right" }}>Calls</th>
            <th style={{ textAlign: "right" }}>Trend</th>
            <th style={{ textAlign: "right" }}>p99</th>
          </tr>
        </thead>
        <tbody>
          {tools && tools.length > 0 ? (
            tools.map(({ name, calls, trend, p99 }) => (
              <tr key={name}>
                <td><span className="tool-name">{name}</span></td>
                <td className="mono" style={{ textAlign: "right" }}>{calls.toLocaleString()}</td>
                <td style={{ textAlign: "right" }}><TinySpark values={trend} /></td>
                <td className="mono" style={{ textAlign: "right", color: "var(--fg)" }}>{p99}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={4} style={{ textAlign: "center", color: "var(--fg-muted)", padding: "24px 0" }}>
                No tools active in this range.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
