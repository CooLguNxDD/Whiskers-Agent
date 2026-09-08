/**
 * MCP vs graph + legacy stacked-plugin traffic cards.
 */
import type { AnalyticsSeries } from "@/api/analytics"
import { CORE_PLUGIN_DISPLAY_NAME } from "@/constants/plugins"
import { MCP_GRAPH_CONFIG, STACKED_PLUGIN_CONFIG } from "../analyticsConfig"
import { McpGraphChart } from "../charts/McpGraphChart"
import { StackedTrafficChart } from "../charts/StackedTrafficChart"

function LegendSwatch({ colorVar, label }: { colorVar: string; label: string }) {
  return (
    <span>
      <span className="sw" style={{ background: colorVar }} />
      {label}
    </span>
  )
}

export function TrafficCards({
  series,
  range,
}: {
  series: AnalyticsSeries | undefined
  range: string
}) {
  return (
    <>
      <div className="ct-traffic-card" style={{ marginBottom: 16 }}>
        <div className="ct-traffic-head">
          <div>
            <div className="ct-eyebrow">Traffic · MCP vs Graph</div>
            <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>
              Primary product axis
              <span style={{ marginLeft: 8, color: "var(--fg-muted)", fontFamily: "var(--font-mono)", fontWeight: 500, fontSize: 12 }}>
                · {range}
              </span>
            </div>
          </div>
          <div className="ct-traffic-legend">
            <LegendSwatch colorVar={MCP_GRAPH_CONFIG.mcp.color} label="mcp calls" />
            <LegendSwatch colorVar={MCP_GRAPH_CONFIG.graph.color} label="graph runs" />
          </div>
        </div>
        <div className="ct-traffic-chart">
          <McpGraphChart series={series} range={range} />
        </div>
      </div>

      <div className="ct-traffic-card" style={{ marginBottom: 16 }}>
        <div className="ct-traffic-head">
          <div>
            <div className="ct-eyebrow">Traffic · stacked by plugin (legacy)</div>
            <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>
              Requests per interval
              <span style={{ marginLeft: 8, color: "var(--fg-muted)", fontFamily: "var(--font-mono)", fontWeight: 500, fontSize: 12 }}>
                · {range}
              </span>
            </div>
          </div>
          <div className="ct-traffic-legend">
            <LegendSwatch colorVar={STACKED_PLUGIN_CONFIG.core.color} label={CORE_PLUGIN_DISPLAY_NAME} />
            <LegendSwatch colorVar={STACKED_PLUGIN_CONFIG.extensions.color} label="extensions" />
            <LegendSwatch colorVar={STACKED_PLUGIN_CONFIG.other.color} label="other" />
          </div>
        </div>
        <div className="ct-traffic-chart">
          <StackedTrafficChart series={series} range={range} />
        </div>
      </div>
    </>
  )
}
