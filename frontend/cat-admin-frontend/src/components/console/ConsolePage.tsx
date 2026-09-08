import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { useMcpState, useSessionStore } from "@/store"
import { usePluginsQuery, useTogglePluginMutation } from "@/hooks/usePlugins"
import { useHealthQuery } from "@/hooks/useHealth"
import AppShell from "@/components/shell/AppShell"
import { Cube, Shield, Capacity, Plus, Activity, Bolt, Lock, Chevron } from "@/components/shell/Icons"
import type { Plugin } from "@/types/plugin"
import { Pagination } from "@/components/Table/Pagination"
import { formatHealthStatus, formatSuccessRate } from "@/utils/healthFormat"
import { PLUGIN_LATENCY_MAP } from "@/constants/plugins"
import { Route } from "@/routes/index"

const buildDirectCompleteUrl = (state: string) =>
  `/oauth/complete-layer1/${encodeURIComponent(state)}`

/**
 * Index route page component.
 *
 * Layer-1 MCP return lands on `/?state=…`. Capture into the persisted session
 * store happens in the route's `beforeLoad` (not a useEffect URL↔Zustand sync
 * loop); this component only clears the query string and reads
 * `useMcpState()` for the banner.
 */
export function ConsolePage() {
  const { data, isPending } = usePluginsQuery()
  const { data: health } = useHealthQuery()
  const { mutate: togglePlugin } = useTogglePluginMutation()
  const plugins = useMemo(() => data?.plugins ?? [], [data?.plugins])
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(10)
  const { oauth_success, state } = Route.useSearch()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const mcpState = useMcpState()
  const clearMcpState = useSessionStore((s) => s.clearMcpState)

  const instanceName = health?.instance_name ?? "…"
  const healthStatus = formatHealthStatus(health?.status)
  const successRateLabel =
    health?.metrics != null ? formatSuccessRate(health.metrics.success_rate) : "…"
  const activeProxies = health?.tunnels?.active
  const pillOk = !health?.status || health.status === "healthy"

  // One-shot URL cleanup after capture (beforeLoad) / oauth_success toast.
  useEffect(() => {
    if (!state && !oauth_success) return
    if (oauth_success) {
      void qc.invalidateQueries({ queryKey: ["plugins"] })
    }
    void navigate({
      to: "/",
      search: { state: undefined, oauth_success: undefined },
      replace: true,
    })
  }, [state, oauth_success, qc, navigate])

  const paginatedPlugins = useMemo(() => {
    const start = (page - 1) * perPage
    return plugins.slice(start, start + perPage)
  }, [plugins, page, perPage])
  const mcpReturn = mcpState
    ? { client: "MCP Client", returnUrl: buildDirectCompleteUrl(mcpState) }
    : null

  return (
    <AppShell active="console" mcpReturn={mcpReturn}>
      <div className="ct-page-head">
        <div>
          <div className="ct-page-title">
            Console
            <span className="ct-eyebrow">/ {instanceName}</span>
          </div>
          <div className="ct-page-sub">Configure and monitor every MCP service running through Whiskers Agent.</div>
        </div>
        <span className={"ct-pill" + (pillOk ? " is-ok" : "")}>
          <span className={"ct-dot" + (pillOk ? " is-ok" : "")} /> {healthStatus} · {successRateLabel} uptime
        </span>
      </div>

      {mcpState && (
        <McpBanner
          returnUrl={buildDirectCompleteUrl(mcpState)}
          client="MCP Client"
          onDismiss={clearMcpState}
        />
      )}

      <div className="ct-stats">
        <StatCard icon={<Cube width="16" height="16" />} label="Total Plugins" value={isPending ? "—" : plugins.length} />
        <StatCard
          icon={<Shield width="16" height="16" />}
          label="Active Proxies"
          value={activeProxies == null ? "—" : activeProxies}
          iconClass="is-neon"
          delta={{ up: true, text: "↑ stable" }}
        />
        <StatCard
          icon={<Capacity width="16" height="16" />}
          label="Tier Capacity"
          value="10,000"
          sub={health?.system_tier_name ?? data?.system_tier_name ?? "LITE"}
          iconClass="is-pink"
          delta={{ text: instanceName }}
        />
      </div>

      <div className="ct-section">
        <div className="ct-section-head">
          <div>
            <div className="ct-section-title">
              Plugin Management
              <span className="ct-pill" style={{ marginLeft: 4 }}>{plugins.length} loaded</span>
            </div>
            <div className="ct-section-sub">Click a plugin to inspect tools, OAuth providers, and config.</div>
          </div>
          <button className="ct-btn-primary">
            <Plus width="14" height="14" /> Add new plugin
          </button>
        </div>
        <div>
          {isPending && [0, 1, 2].map((i) => (
            <div key={i} className="ct-plugin" style={{ opacity: 0.5 }}>
              <div className="ct-plugin-ico" />
              <div style={{ height: 16, background: "var(--bg-sunken)", borderRadius: 4, width: "60%" }} />
            </div>
          ))}
          {!isPending && (
            <>
              <div>
                {paginatedPlugins.length === 0 ? (
                  <div style={{ padding: "40px 18px", textAlign: "center", color: "var(--fg-muted)", fontFamily: "var(--font-mono)", fontSize: "12px" }}>
                    no plugins yet
                  </div>
                ) : (
                  paginatedPlugins.map((p) => (
                    <PluginRow key={p.id} plugin={p} onToggle={() => togglePlugin({ id: p.id, enabled: !p.enabled })} />
                  ))
                )}
              </div>
              <Pagination
                total={plugins.length}
                page={page}
                perPage={perPage}
                onPageChange={setPage}
                onPerPageChange={(n) => { setPerPage(n); setPage(1) }}
              />
            </>
          )}
        </div>
      </div>
      <div style={{ height: 12 }} />
    </AppShell>
  )
}

function StatCard({ icon, label, value, sub, iconClass = "", delta }: {
  icon: React.ReactNode; label: string; value: string | number; sub?: string; iconClass?: string; delta?: { up?: boolean; text: string }
}) {
  return (
    <div className="ct-stat">
      <div className="ct-stat-head">
        <div className={"ct-stat-ico " + iconClass}>{icon}</div>
        {delta && <div className={"ct-stat-delta" + (delta.up ? " is-up" : "")}>{delta.text}</div>}
      </div>
      <div className="ct-stat-label">{label}</div>
      <div className="ct-stat-value">
        {value}
        {sub && <span className="ct-stat-sub">{sub}</span>}
      </div>
    </div>
  )
}

/**
 * Component representing a single plugin row in the list.
 */
function PluginRow({ plugin, onToggle }: { plugin: Plugin; onToggle: () => void }) {
  const navigate = useNavigate()
  const usage = useMemo(() => {
    let hash = 0
    for (let i = 0; i < plugin.id.length; i++) {
      hash = plugin.id.charCodeAt(i) + ((hash << 5) - hash)
    }
    return Math.abs(hash) % 60 + 5
  }, [plugin.id])

  const latency = useMemo(() => {
    const key = Object.keys(PLUGIN_LATENCY_MAP).find(
      (id) => plugin.name === id || plugin.id.includes(id)
    )
    return key ? PLUGIN_LATENCY_MAP[key] : "~"
  }, [plugin.name, plugin.id])

  return (
    <div
      className="ct-plugin"
      role="button"
      tabIndex={0}
      onClick={() => void navigate({ to: "/plugins/$pluginId", params: { pluginId: plugin.id } })}
      onKeyDown={(e) => {
        // Prevent event bubbling from child elements like the toggle switch
        if (e.target !== e.currentTarget) return
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          void navigate({ to: "/plugins/$pluginId", params: { pluginId: plugin.id } })
        }
      }}
    >
      <div className="ct-plugin-ico"><Bolt width="20" height="20" /></div>
      <div style={{ minWidth: 0 }}>
        <div className="ct-plugin-name">
          {plugin.name}
          <span className={"ct-tier " + (plugin.tier === "pro" ? "is-pro" : "is-lite")}>{plugin.tier}</span>
        </div>
        <div className="ct-plugin-meta" style={{ minWidth: 0 }}>
          <span>v{plugin.version}</span>
          <span className="dot">·</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
            <Activity width="11" height="11" /> {latency}
          </span>
          <span className="dot" style={{ flexShrink: 0 }}>·</span>
          <span style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-sans)", textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap", minWidth: 0 }}>{plugin.description}</span>
        </div>
      </div>
      <div className="ct-usage">
        <div className="ct-usage-head">
          <span>Usage</span>
          <span style={{ color: usage >= 70 ? "var(--warn)" : "var(--fg-muted)" }}>{usage}%</span>
        </div>
        <div className="ct-usage-bar">
          <div
            className={"ct-usage-fill" + (usage >= 80 ? " is-crit" : usage >= 60 ? " is-high" : "")}
            style={{ width: `${Math.max(2, usage)}%` }}
          />
        </div>
      </div>
      <button
        className={"ct-switch" + (plugin.enabled ? " is-on" : "")}
        role="switch"
        aria-checked={plugin.enabled}
        onClick={(e) => { e.stopPropagation(); onToggle() }}
        onKeyDown={(e) => e.stopPropagation()}
        aria-label="toggle"
      />
      <Chevron width="14" height="14" className="ct-chev" />
    </div>
  )
}

function McpBanner({ returnUrl, client, onDismiss }: { returnUrl: string; client: string; onDismiss: () => void }) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: "grid", gridTemplateColumns: "auto 1fr auto auto", alignItems: "center", gap: 16,
        padding: "14px 16px 14px 14px", marginBottom: 18, borderRadius: 12, position: "relative", overflow: "hidden",
        background: "linear-gradient(135deg, color-mix(in oklch, var(--amber) 14%, var(--card)) 0%, var(--card) 60%)",
        border: "1px solid color-mix(in oklch, var(--amber) 35%, var(--border))",
        boxShadow: "inset 0 1px 0 color-mix(in oklch, var(--amber) 18%, transparent), 0 0 24px -10px color-mix(in oklch, var(--amber) 55%, transparent)",
      }}
    >
      <div style={{ width: 38, height: 38, borderRadius: 10, display: "grid", placeItems: "center", background: "linear-gradient(135deg, color-mix(in oklch, var(--amber) 30%, var(--bg-sunken)), var(--bg-sunken))", border: "1px solid color-mix(in oklch, var(--amber) 40%, var(--border))", color: "var(--amber)", boxShadow: "var(--glow-amber)" }}>
        <Lock width="16" height="16" />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="ct-eyebrow" style={{ color: "var(--amber)", letterSpacing: "0.16em" }}>mcp authorization</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--fg-subtle)", display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 6, height: 6, borderRadius: 999, background: "var(--neon)", boxShadow: "0 0 8px var(--neon)" }} />
            ready to hand off
          </span>
        </div>
        <div style={{ fontSize: 13.5, color: "var(--fg)", lineHeight: 1.4 }}>
          Server registered. Return to <code style={{ fontFamily: "var(--font-mono)", background: "var(--bg-sunken)", border: "1px solid var(--hairline)", borderRadius: 4, padding: "1px 6px", fontSize: 12, color: "var(--fg)" }}>{client}</code> to finish the connection.
        </div>
      </div>
      <button type="button" onClick={onDismiss} className="ct-btn-ghost" style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontSize: 10.5, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--fg-muted)" }}>
        later
      </button>
      <a href={returnUrl} className="ct-btn-primary" style={{ padding: "10px 14px", fontSize: 13, textDecoration: "none", whiteSpace: "nowrap" }}>
        Return to MCP client →
      </a>
    </div>
  )
}
