/**
 * Five-stat KPI strip for the analytics dashboard.
 */
import { Activity, Spark, Clock, Globe } from "@/components/shell/Icons"
import type { OverlayKpis } from "../overlay"

export function KpiStrip({ kpis, range }: { kpis: OverlayKpis; range: string }) {
  return (
    <div className="ct-stats is-5">
      <div className="ct-stat">
        <div className="ct-stat-head">
          <div className="ct-stat-ico is-pink"><Activity width="16" height="16" /></div>
          <div className="ct-stat-delta">historic</div>
        </div>
        <div className="ct-stat-label">MCP calls · {range}</div>
        <div className="ct-stat-value">{kpis.totalCalls.toLocaleString()}<span className="ct-stat-sub">/ range</span></div>
      </div>
      <div className="ct-stat">
        <div className="ct-stat-head">
          <div className="ct-stat-ico is-cyan"><Activity width="16" height="16" /></div>
          <div className="ct-stat-delta">historic</div>
        </div>
        <div className="ct-stat-label">Graph runs · {range}</div>
        <div className="ct-stat-value">{kpis.graphCalls.toLocaleString()}<span className="ct-stat-sub">/ {kpis.graphSuccessRate.toFixed(0)}% ok</span></div>
      </div>
      <div className="ct-stat">
        <div className="ct-stat-head">
          <div className="ct-stat-ico is-neon"><Spark width="16" height="16" /></div>
          <div className="ct-stat-delta is-up">↑ stable</div>
        </div>
        <div className="ct-stat-label">Success rate</div>
        <div className="ct-stat-value">{kpis.successRate.toFixed(2)}<span className="ct-stat-sub">%</span></div>
      </div>
      <div className="ct-stat">
        <div className="ct-stat-head">
          <div className="ct-stat-ico"><Clock width="16" height="16" /></div>
          <div className="ct-stat-delta">p50 / p99</div>
        </div>
        <div className="ct-stat-label">Latency</div>
        <div className="ct-stat-value">{kpis.p50}<span className="ct-stat-sub">/ {kpis.p99} ms</span></div>
      </div>
      <div className="ct-stat">
        <div className="ct-stat-head">
          <div className="ct-stat-ico is-cyan"><Globe width="16" height="16" /></div>
          <div className="ct-stat-delta is-up">live</div>
        </div>
        <div className="ct-stat-label">Active sessions</div>
        <div className="ct-stat-value">{kpis.activeSessions}<span className="ct-stat-sub">connected</span></div>
      </div>
    </div>
  )
}
