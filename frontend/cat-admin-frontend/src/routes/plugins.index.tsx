import { useState, useMemo } from "react"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { usePluginsQuery, useTogglePluginMutation, useReindexPluginMutation } from "@/hooks/usePlugins"
import AppShell from "@/components/shell/AppShell"
import { Bolt, Search, Refresh, Plus, Chevron } from "@/components/shell/Icons"
import type { Plugin } from "@/types/plugin"
import { Table } from "@/components/Table/Table"
import { Pagination } from "@/components/Table/Pagination"

/**
 * Plugins index route configuration.
 */
export const Route = createFileRoute("/plugins/")({
  component: function PluginListPage() {
    const { data, isPending } = usePluginsQuery()
    const { mutate: togglePlugin } = useTogglePluginMutation()
    const navigate = useNavigate()

    const [page, setPage] = useState(1)
    const [perPage, setPerPage] = useState(10)
    const [query, setQuery] = useState("")
    const [filter, setFilter] = useState("all")

    const allPlugins = useMemo(() => data?.plugins ?? [], [data?.plugins])
    const activeCount = useMemo(() => allPlugins.filter((p) => p.enabled).length, [allPlugins])

    const filtered = useMemo(() => {
      return allPlugins.filter((p) => {
        const q = query.trim().toLowerCase()
        if (q && !(p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q))) return false
        if (filter === "enabled" && !p.enabled) return false
        if (filter === "disabled" && p.enabled) return false
        if (filter === "pro" && p.tier !== "pro") return false
        if (filter === "lite" && p.tier === "pro") return false
        return true
      })
    }, [allPlugins, query, filter])

    const { mutate: reindexPlugin, isPending: isReindexing, variables: reindexingId } = useReindexPluginMutation()

    const columns = useMemo(() => [
      {
        key: "icon",
        header: "",
        width: "44px",
        truncate: false,
        render: () => (
          <div className="ct-plugin-ico"><Bolt width="20" height="20" /></div>
        ),
      },
      {
        key: "name",
        header: "Plugin",
        width: "1.3fr",
        sortable: true,
        sortValue: (p: Plugin) => p.name,
        truncate: false,
        render: (plugin: Plugin) => (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13.5, fontWeight: 600 }}>{plugin.name}</span>
            {!plugin.enabled && <span className="ct-pill" style={{ fontSize: 9.5, padding: "1px 6px" }}>off</span>}
          </div>
        ),
      },
      {
        key: "tier",
        header: "Tier",
        width: "110px",
        sortable: true,
        sortValue: (p: Plugin) => p.tier,
        render: (plugin: Plugin) => (
          <span className={"ct-tier " + (plugin.tier === "pro" ? "is-pro" : "is-lite")}>{plugin.tier}</span>
        ),
      },
      {
        key: "version",
        header: "Version",
        width: "110px",
        sortable: true,
        sortValue: (p: Plugin) => p.version,
        render: (plugin: Plugin) => (
          <span className="ct-mono-meta">v{plugin.version}</span>
        ),
      },
      {
        key: "description",
        header: "Description",
        width: "1.6fr",
        render: (plugin: Plugin) => (
          <span className="ct-mono-meta" style={{ fontSize: 11, color: "var(--fg-subtle)", fontFamily: "var(--font-sans)" }}>{plugin.description}</span>
        ),
      },
      {
        key: "actions",
        header: "On",
        width: "90px",
        align: "center" as const,
        truncate: false,
        render: (plugin: Plugin) => (
          <div
            style={{ display: "flex", alignItems: "center", gap: 12, justifySelf: "center" }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {plugin.enabled && (
              <button
                className="ct-btn-ghost"
                style={{ padding: 4, height: "auto", borderRadius: 4 }}
                onClick={() => reindexPlugin(plugin.id)}
                title="Reindex routes"
                disabled={isReindexing && reindexingId === plugin.id}
              >
                <Refresh
                  width="13"
                  height="13"
                  className={isReindexing && reindexingId === plugin.id ? "animate-spin text-primary" : "text-fg-subtle hover:text-fg"}
                />
              </button>
            )}
            <button
              className={"ct-switch" + (plugin.enabled ? " is-on" : "")}
              role="switch"
              aria-checked={plugin.enabled}
              onClick={() => togglePlugin({ id: plugin.id, enabled: !plugin.enabled })}
              aria-label="toggle"
            />
          </div>
        ),
      },
      {
        key: "chevron",
        header: "",
        width: "24px",
        truncate: false,
        render: () => <Chevron width="14" height="14" className="ct-chev" />,
      },
    ], [isReindexing, reindexingId, reindexPlugin, togglePlugin])

    return (
      <AppShell active="plugins">
        <div className="ct-page-head">
          <div>
            <div className="ct-page-title">
              Plugins
              <span className="ct-eyebrow">/ {allPlugins.length} loaded · {activeCount} active</span>
            </div>
            <div className="ct-page-sub">All MCP services registered to this node. Click a row to inspect.</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ct-btn-ghost">
              <Refresh width="13" height="13" style={{ marginRight: 6 }} /> Sync registry
            </button>
            <button className="ct-btn-primary" onClick={() => navigate({ to: "/proxies" })}>
              <Plus width="13" height="13" /> Add plugin
            </button>
          </div>
        </div>

        <div className="ct-section">
          <div className="ct-filter-bar">
            <div className="ct-filter-search">
              <Search />
              <input
                placeholder="Filter by name or description…"
                value={query}
                onChange={(e) => { setQuery(e.target.value); setPage(1) }}
              />
            </div>
            {(["all", "enabled", "disabled", "pro", "lite"] as const).map((k) => (
              <button
                key={k}
                className={"ct-chip" + (filter === k ? " is-active" : "")}
                onClick={() => { setFilter(k); setPage(1) }}
              >
                {k}
                {filter === k && k !== "all" && <span className="ct-chip-x">×</span>}
              </button>
            ))}
          </div>

          {isPending && [0, 1, 2].map((i) => (
            <div key={i} className="ct-plugin-row ct-plugin-grid" style={{ opacity: 0.4 }}>
              {Array.from({ length: 7 }).map((_, j) => <div key={j} style={{ height: 14, background: "var(--bg-sunken)", borderRadius: 4 }} />)}
            </div>
          ))}

          {!isPending && (
            <>
              <Table
                columns={columns}
                rows={filtered}
                page={page}
                perPage={perPage}
                rowKey={(p) => p.id}
                onRowClick={(p) => void navigate({ to: "/plugins/$pluginId", params: { pluginId: p.id } })}
                emptyMessage="no plugins match this filter"
              />
              <Pagination
                total={filtered.length}
                page={page}
                perPage={perPage}
                onPageChange={setPage}
                onPerPageChange={(n) => { setPerPage(n); setPage(1) }}
                filteredNote={filter !== "all" ? { shown: filtered.length, ofTotal: allPlugins.length } : undefined}
              />
            </>
          )}
        </div>
        <div style={{ height: 12 }} />
      </AppShell>
    )
  },
})
