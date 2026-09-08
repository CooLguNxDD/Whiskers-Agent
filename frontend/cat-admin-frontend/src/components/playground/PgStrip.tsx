/**
 * PgStrip Component
 *
 * A compact toolbar header replacing the old server selection bar and large mode cards.
 * Integrates the playground mode tabs, active server selection dropdown, and panel toggles.
 */

import { useMemo, useState } from "react"
import { ChevronDown, Activity, Refresh, Plus } from "@/components/shell/Icons"
import { PG_SERVERS } from "./constants"
import type { PGServer } from "./types"
import { useHealthQuery } from "@/hooks/useHealth"

function ServerPill({
  server,
  active,
  onClick,
}: {
  server: PGServer
  active: boolean
  onClick: () => void
}) {
  const dotCls =
    server.status === "ok"
      ? "is-ok"
      : server.status === "warn"
        ? "is-warn"
        : "is-off"
  return (
    <button
      className={"pg-server" + (active ? " is-active" : "")}
      onClick={onClick}
      title={server.url}
    >
      <span className={"ct-dot " + dotCls} />
      <div>
        <span className="pg-server-label">{server.label}</span>
        <span className="pg-server-url" style={{ display: "block" }}>
          {server.url}
        </span>
      </div>
      <span className="pg-server-meta">
        {server.transport} · {server.latency}
      </span>
    </button>
  )
}

interface PgStripProps {
  mode: "mcp" | "tool" | "group"
  onModeChange: (mode: "mcp" | "tool" | "group") => void
  serverId: string
  onPick: (id: string) => void
}

/**
 * Playground strip header component.
 */
export function PgStrip({
  mode,
  onModeChange,
  serverId,
  onPick,
}: PgStripProps) {
  const [serverOpen, setServerOpen] = useState(false)
  const [customUrl, setCustomUrl] = useState("")
  const { data: health } = useHealthQuery()

  // Overlay live instance_name onto the local server profile label.
  const servers = useMemo(() => {
    const instance = health?.instance_name
    if (!instance) return PG_SERVERS
    return PG_SERVERS.map((s) =>
      s.id === "local" ? { ...s, label: instance } : s,
    )
  }, [health?.instance_name])

  const activeServer = servers.find((x) => x.id === serverId) ?? servers[0]
  const dotCls =
    activeServer.status === "ok"
      ? "is-ok"
      : activeServer.status === "warn"
        ? "is-warn"
        : "is-off"

  const MODES = [
    { id: "mcp", label: "MCP" },
    { id: "tool", label: "Tool Test" },
    { id: "group", label: "Group Test" },
  ] as const

  return (
    <div className="pg-strip">
      {/* Mode Tabs */}
      <div className="pg-strip-tabs">
        {MODES.map((m) => (
          <button
            key={m.id}
            className={"pg-stab" + (mode === m.id ? " is-active" : "")}
            onClick={() => onModeChange(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="pg-strip-sep" />

      {/* Server Selector Dropdown */}
      <div style={{ position: "relative" }}>
        <button
          className={"pg-srv-btn" + (serverOpen ? " is-open" : "")}
          onClick={() => setServerOpen((v) => !v)}
        >
          <span className={"ct-dot " + dotCls} />
          <span className="pg-srv-label">{activeServer.label}</span>
          <span className="pg-srv-url">{activeServer.url}</span>
          <ChevronDown
            width="11"
            height="11"
            style={{
              color: "var(--fg-subtle)",
              flexShrink: 0,
              transition: "transform 150ms",
              transform: serverOpen ? "rotate(180deg)" : "none",
            }}
          />
        </button>

        {serverOpen && (
          <div className="pg-server-pop" style={{ top: "calc(100% + 8px)", left: 0 }}>
            <div className="ct-search-section">
              <span>Saved profiles</span>
              <span className="count">{servers.length}</span>
            </div>
            <div className="pg-server-list">
              {servers.map((x) => (
                <ServerPill
                  key={x.id}
                  server={x}
                  active={x.id === serverId}
                  onClick={() => {
                    onPick(x.id)
                    setServerOpen(false)
                  }}
                />
              ))}
            </div>
            <div className="pg-server-add">
              <input
                className="ct-input"
                placeholder="https://your-server.example/mcp"
                value={customUrl}
                onChange={(e) => setCustomUrl(e.target.value)}
                style={{ flex: 1, fontSize: 12 }}
              />
              <button className="ct-btn-primary" style={{ fontSize: 12 }}>
                <Plus width="13" height="13" /> Connect
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Status Pills */}
      <div className="pg-strip-pills">
        <span className="ct-pill is-ok">
          <span className="ct-dot is-ok" /> stream open · {activeServer.transport}
        </span>
        <span className="ct-pill">
          <Activity width="11" height="11" /> {activeServer.latency}
        </span>
        <span className="ct-pill">
          <span style={{ color: "var(--amber)", fontWeight: 700 }}>
            {servers.length * 2}
          </span>
          &nbsp;tools
        </span>
        <button className="ct-btn-ghost" style={{ padding: "4px 8px" }}>
          <Refresh width="12" height="12" />
        </button>
      </div>
    </div>
  )
}
