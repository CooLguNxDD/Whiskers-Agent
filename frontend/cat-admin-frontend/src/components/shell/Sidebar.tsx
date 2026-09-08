/**
 * Sidebar Component
 *
 * The main vertical navigation bar for the application.
 * Contains workspace links, live connection status, and MCP return-to-client redirects.
 */

import { useEffect, useState } from "react"
import { motion } from "framer-motion"
import BrandMark from "./BrandMark"
import { Grid, Cube, Flask, Activity, Gear, Lock, Eye, Bolt, Key } from "./Icons"
import { useAuthState, useUIStore } from "@/store"
import { bus } from "@/events/bus"
import { useHealthQuery } from "@/hooks/useHealth"
import { kbdModifier } from "@/utils/kbdModifier"
import {
  formatIdentity,
  formatP50,
  formatSessionAge,
  formatSuccessRate,
} from "@/utils/healthFormat"

interface McpReturn {
  client: string
  returnUrl: string
}

interface SidebarProps {
  active: string
  onNav: (id: string) => void
  connected?: boolean
  mcpReturn?: McpReturn | null
  onReconnect?: () => void
}

const NAV_ITEMS = [
  { id: "console",    label: "Console",          Icon: Grid,     key: "1" },
  { id: "plugins",    label: "Plugins",          Icon: Cube,     key: "2" },
  { id: "proxies",    label: "Proxies",          Icon: Eye,      key: "3" },
  { id: "terminal",   label: "Terminal",         Icon: Bolt,     key: "4" },
  { id: "playground", label: "Playground",       Icon: Flask,    key: "5" },
  { id: "analytics",  label: "Analytics", Icon: Activity, key: "6" },
  { id: "config",     label: "System Config",    Icon: Gear,     key: "7" },
  { id: "api-keys",   label: "API Keys",         Icon: Key,      key: "8" },
]

/**
 * Renders the primary sidebar navigation.
 */
export default function Sidebar({ active, onNav, connected = true, mcpReturn, onReconnect }: SidebarProps) {
  const kbdMod = kbdModifier()
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)
  const { subject, iat } = useAuthState()
  const { data: health, isError: healthError, isFetching } = useHealthQuery()

  // Tick session age every 30s so the "14m" label stays fresh.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(id)
  }, [])

  // Trigger refresh on specific global events (like connection drops)
  useEffect(() => {
    const handleRefresh = () => {
      // Force a re-render or refetch by updating a tick
      setNow(Date.now())
    }
    bus.on("sidebar:refresh", handleRefresh)
    return () => bus.off("sidebar:refresh", handleRefresh)
  }, [])

  const pluginsTotal = health?.plugins?.total
  const proxiesTotal = health?.tunnels?.total
  const successRate = health?.metrics?.success_rate
  const p50 = health?.metrics?.p50_latency_ms
  const instanceName = health?.instance_name ?? "local"

  // Connected only when auth says so; health errors don't force disconnect.
  const isConnected = connected && !healthError

  const liveCounts =
    pluginsTotal != null && proxiesTotal != null
      ? `${pluginsTotal} plugins · ${proxiesTotal} proxies`
      : isFetching
        ? "… plugins · … proxies"
        : "— plugins · — proxies"

  const liveMetrics =
    successRate != null && p50 != null
      ? `uptime ${formatSuccessRate(successRate)} · ${formatP50(p50)} p50`
      : isFetching
        ? "uptime … · … p50"
        : "uptime — · — p50"

  const identity = formatIdentity(subject, instanceName)
  const sessionAge = formatSessionAge(iat, now)

  return (
    <motion.aside layout initial={false} animate={{ width: sidebarCollapsed ? 48 : 240 }} transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }} className={"ct-side" + (sidebarCollapsed ? " is-collapsed" : "")} style={{ overflow: "hidden" }}>
      <div className="ct-brand">
        <BrandMark />
        <motion.div layout initial={false} animate={{ opacity: sidebarCollapsed ? 0 : 1 }} transition={{ duration: 0.15 }} className="ct-brand-text" style={{ whiteSpace: "nowrap" }}>
          <div className="ct-brand-name">
            whiskers<span style={{ color: "var(--amber)" }}>.agent</span>
          </div>
          <div className="ct-brand-sub">mcp · admin</div>
        </motion.div>
      </div>

      <motion.div layout animate={{ opacity: sidebarCollapsed ? 0 : 1 }} transition={{ duration: 0.15 }} className="ct-nav-section" style={{ whiteSpace: "nowrap" }}>Workspace</motion.div>
      <nav className="ct-nav">
        {NAV_ITEMS.map(({ id, label, Icon, key }) => (
          <button
            key={id}
            className={"ct-nav-item" + (active === id ? " is-active" : "")}
            onClick={() => onNav(id)}
          >
            <Icon />
            <span>{label}</span>
            <span className="ct-kbd" data-kbd={`nav-${id}`}>{kbdMod}{key}</span>
          </button>
        ))}
      </nav>

      <motion.div layout animate={{ opacity: sidebarCollapsed ? 0 : 1 }} transition={{ duration: 0.15 }} className="ct-side-extra" style={{ whiteSpace: "nowrap", overflow: "hidden" }}>
        <div className="ct-nav-section" style={{ marginTop: 10 }}>Live</div>
        {mcpReturn && (
          <nav className="ct-nav" style={{ marginBottom: 4 }}>
            <a
              href={mcpReturn.returnUrl}
              className="ct-nav-item ct-nav-mcp"
              style={{
                textDecoration: "none",
                background: "linear-gradient(90deg, color-mix(in oklch, var(--amber) 22%, transparent), color-mix(in oklch, var(--amber) 6%, transparent))",
                boxShadow: "inset 0 0 0 1px color-mix(in oklch, var(--amber) 35%, transparent)",
                color: "var(--fg)",
              }}
              title={`Complete MCP registration with ${mcpReturn.client}`}
            >
              <Lock />
              <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.15, gap: 1 }}>
                <span>Return to client</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--amber)", letterSpacing: "0.06em", textTransform: "lowercase", fontWeight: 500 }}>
                  {mcpReturn.client}
                </span>
              </span>
              <span
                className="ct-kbd"
                style={{ background: "color-mix(in oklch, var(--amber) 18%, var(--bg-sunken))", color: "var(--amber)", borderColor: "color-mix(in oklch, var(--amber) 35%, var(--border))", display: "inline-flex", alignItems: "center", gap: 4 }}
              >
                <span style={{ width: 5, height: 5, borderRadius: 999, background: "var(--amber)", boxShadow: "0 0 6px var(--amber)", animation: "ct-pulse-dot 1.4s ease-in-out infinite" }} />
                ready
              </span>
            </a>
          </nav>
        )}
        <div style={{ padding: "0 10px 4px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--fg-muted)" }}>
            <span className="ct-status-dot" /> {liveCounts}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, fontSize: 11.5, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>
            {liveMetrics}
          </div>
        </div>

        <div className="ct-side-foot">
          <div className="ct-side-foot-label">
            <span>Status</span>
            <Eye width="11" height="11" />
          </div>
          <div className="ct-side-foot-status">
            <span className={"ct-status-dot" + (isConnected ? "" : " is-off")} />
            <span>{isConnected ? "Connected" : "Disconnected"}</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--fg-muted)", marginTop: 6, fontFamily: "var(--font-mono)" }}>
            {isConnected ? `${identity} · ${sessionAge}` : "session expired"}
          </div>
          {!isConnected && (
            <button
              type="button"
              className="ct-btn-ghost"
              style={{ marginTop: 10, width: "100%", justifyContent: "center", display: "flex" }}
              onClick={() => onReconnect?.()}
            >
              Reconnect
            </button>
          )}
        </div>
      </motion.div>
    </motion.aside>
  )
}
