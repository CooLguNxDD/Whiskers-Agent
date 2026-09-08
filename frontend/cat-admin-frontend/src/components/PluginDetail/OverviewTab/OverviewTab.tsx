/**
 * OverviewTab Component
 *
 * Dashboard view presenting the overall status, metrics, and health of a specific plugin.
 */
import type { FC } from "react"
import { cn } from "@/lib/utils"
import type { Plugin, PluginHealth } from "@/types/plugin"

export interface OverviewTabProps {
  plugin: Plugin
  healthData?: PluginHealth
  className?: string
  onTabChange?: (tab: "overview" | "tools" | "actions" | "logs" | "config" | "skills") => void
}

const OverviewTab: FC<OverviewTabProps> = ({ plugin, healthData, className, onTabChange }) => {
  return (
    <div className={cn("ct-grid-2", className)}>
      <div className="ct-panel">
        <div className="ct-panel-head"><span className="ct-panel-title">Metadata</span></div>
        <div className="ct-panel-body">
          <dl className="ct-kv">
            <dt>Plugin ID</dt><dd>{plugin.id}</dd>
            <dt>Version</dt><dd>{plugin.version}</dd>
            <dt>Tier</dt><dd>{plugin.tier}</dd>
            <dt>Status</dt><dd>{plugin.enabled ? "enabled" : "disabled"}</dd>
          </dl>
        </div>
      </div>
      <div className="ct-panel">
        <div className="ct-panel-head"><span className="ct-panel-title">Health</span></div>
        <div className="ct-panel-body">
          {healthData ? (
            <div className="ct-health-grid">
              <div className="ct-health">
                <div className="ct-health-label">Lifecycle</div>
                <div className="ct-health-value">
                  <span className={"ct-dot " + (healthData.lifecycle_state === "loaded" ? "is-ok" : "is-off")} />
                  {healthData.lifecycle_state}
                </div>
              </div>
              <div className="ct-health">
                <div className="ct-health-label">Credentials</div>
                <div className="ct-health-value">
                  <span className={"ct-dot " + (healthData.credentials_present ? "is-ok" : "is-err")} />
                  {healthData.credentials_present ? "present" : "missing"}
                </div>
              </div>
              <div
                className={cn("ct-health", onTabChange && "is-clickable")}
                onClick={() => onTabChange?.("tools")}
              >
                <div className="ct-health-label">Tools</div>
                <div className="ct-health-value">{healthData.tools_count}</div>
              </div>
            </div>
          ) : (
            <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>Loading…</div>
          )}
          {plugin.capabilities && plugin.capabilities.length > 0 && (
            <div className="ct-caps" style={{ marginTop: 14 }}>
              {plugin.capabilities.map((c: string) => <span key={c} className="ct-cap">{c}</span>)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * OverviewTab Component.
 * Renders the UI and handles state for the OverviewTab feature.
 */
export default OverviewTab
