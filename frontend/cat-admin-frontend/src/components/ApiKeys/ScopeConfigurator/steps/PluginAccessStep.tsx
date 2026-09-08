/**
 * PluginAccessStep — Pane 2 of the scope configurator: per-plugin
 * full-access switch and granular group/op token controls.
 */

import { useState } from "react"
import { Layers, ChevronDown, ChevronUp, Sliders } from "lucide-react"
import type { PluginScopeContribution } from "@/api/apiKeys"

interface PluginAccessStepProps {
  plugins: Array<{ id: string; name?: string }>
  selectedScopes: string[] | null
  expandedPlugins: Record<string, boolean>
  getPluginGroups: (pluginId: string, idx: number) => string[]
  isPluginWideSelected: (pluginId: string) => boolean
  onToggleExpand: (pluginId: string) => void
  onTogglePluginWide: (pluginId: string) => void
  onToggleScope: (scope: string) => void
  pluginScopesList?: PluginScopeContribution[]
}

/** PluginAccessStep component for Pane 2 of the scope configurator. */
export function PluginAccessStep({
  plugins,
  selectedScopes,
  expandedPlugins,
  getPluginGroups,
  isPluginWideSelected,
  onToggleExpand,
  onTogglePluginWide,
  onToggleScope,
  pluginScopesList = [],
}: PluginAccessStepProps) {
  const isAllAccess =
    selectedScopes === null ||
    (selectedScopes.length === 1 &&
      (selectedScopes[0] === "all" || selectedScopes[0] === "*"))

  // Combine plugins from list and pluginScopesList
  const allPluginIds = Array.from(
    new Set([
      ...plugins.map((p) => p.id),
      ...pluginScopesList.map((ps) => ps.plugin_id).filter((pid) => pid && pid !== "core"),
    ])
  ).sort()

  const pluginMap = new Map<string, { id: string; name?: string }>()
  plugins.forEach((p) => pluginMap.set(p.id, p))

  return (
    <div className="ct-panel bg-card border border-border rounded-xl shadow-sm overflow-hidden flex flex-col">
      <div className="p-5 border-b border-hairline ct-surface-sunken flex items-center justify-between">
        <div>
          <h3 className="text-sm font-bold text-foreground flex items-center gap-2">
            <Layers className="size-4 text-[var(--amber)]" />
            Level 2 — Plugin Scopes
          </h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Configure full plugin access or granular route group and operation tokens per plugin.
          </p>
        </div>
        <span className="ct-scope-chip font-mono">
          {allPluginIds.length} Plugins
        </span>
      </div>

      {allPluginIds.length === 0 ? (
        <div className="p-6 text-xs text-muted-foreground font-mono text-center">
          No active plugins installed.
        </div>
      ) : (
        <div className="divide-y divide-hairline">
          {allPluginIds.map((pluginId, idx) => {
            const plugin = pluginMap.get(pluginId) || { id: pluginId, name: pluginId }
            const isExpanded = !!expandedPlugins[pluginId]
            const isWide = isPluginWideSelected(pluginId)

            // Get contributed scopes for this plugin
            const contributed = pluginScopesList.filter((ps) => ps.plugin_id === pluginId)
            const groupFallback = getPluginGroups(pluginId, idx).map((g) => `group:${pluginId}:${g}`)
            const tokens = contributed.length > 0
              ? contributed.map((c) => c.token).filter((t) => t !== `plugin:${pluginId}`)
              : groupFallback

            const selectedChildCount = isAllAccess
              ? tokens.length
              : tokens.filter((t) => selectedScopes?.includes(t)).length

            return (
              <div key={pluginId} className="bg-transparent border-b border-hairline last:border-b-0">
                <div className="px-5 py-4 flex items-center justify-between hover:bg-[color-mix(in_oklch,var(--fg)_4%,transparent)] transition-colors">
                  <button
                    type="button"
                    onClick={() => onToggleExpand(pluginId)}
                    className="flex items-center gap-3 bg-transparent border-0 cursor-pointer text-left focus:outline-none flex-1"
                  >
                    <Sliders className="size-4 text-muted-foreground" />
                    <div>
                      <span className="text-xs font-mono font-bold text-foreground">{plugin.name || pluginId}</span>
                      {plugin.name && plugin.name !== pluginId && (
                        <span className="text-[10px] text-muted-foreground font-mono ml-2">({pluginId})</span>
                      )}
                    </div>
                  </button>

                  <div className="flex items-center gap-4">
                    {/* Full access switch */}
                    <label className="flex items-center gap-2 text-xs font-mono cursor-pointer">
                      <span className="text-[11px] text-muted-foreground">Full plugin access</span>
                      <input
                        type="checkbox"
                        checked={isWide || isAllAccess}
                        onChange={() => onTogglePluginWide(pluginId)}
                        className="accent-[var(--amber)] rounded cursor-pointer"
                      />
                    </label>

                    <button
                      type="button"
                      onClick={() => onToggleExpand(pluginId)}
                      className="flex items-center gap-2 bg-transparent border-0 cursor-pointer p-1 text-muted-foreground hover:text-foreground"
                    >
                      <span
                        className={`text-[10px] font-mono px-2 py-0.5 rounded border transition-colors ${
                          isWide || selectedChildCount > 0 ? "ct-tag-amber" : "ct-scope-chip"
                        }`}
                      >
                        {isWide ? "full" : `${selectedChildCount}/${tokens.length}`}
                      </span>
                      {isExpanded ? (
                        <ChevronUp className="size-3.5" />
                      ) : (
                        <ChevronDown className="size-3.5" />
                      )}
                    </button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="px-5 pb-5 pt-2 bg-[color-mix(in_oklch,var(--fg)_4%,transparent)] border-t border-hairline/30 space-y-3">
                    {tokens.length === 0 ? (
                      <div className="text-[11px] text-muted-foreground italic p-2 font-mono">
                        No sub-scopes registered for this plugin. Full plugin access token: plugin:{pluginId}
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        {tokens.map((token) => {
                          const isSelected = isAllAccess || isWide || (selectedScopes ? selectedScopes.includes(token) : false)
                          const meta = contributed.find((c) => c.token === token)

                          return (
                            <label
                              key={token}
                              className={`flex items-start gap-3 cursor-pointer p-2.5 rounded-lg border transition-colors ${
                                isSelected
                                  ? "border-[color-mix(in_oklch,var(--amber)_35%,var(--hairline))] bg-[color-mix(in_oklch,var(--amber)_8%,transparent)] text-foreground"
                                  : "border-border bg-transparent text-muted-foreground hover:bg-[color-mix(in_oklch,var(--fg)_6%,transparent)]"
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={isSelected}
                                disabled={isWide && !isAllAccess}
                                onChange={() => onToggleScope(token)}
                                className="accent-[var(--amber)] rounded mt-0.5 cursor-pointer"
                              />
                              <div className="flex flex-col">
                                <span className={`text-[11px] font-mono font-medium ${isSelected ? "text-[var(--amber)]" : ""}`}>
                                  {token}
                                </span>
                                {meta?.description && (
                                  <span className="text-[10px] text-muted-foreground mt-0.5">
                                    {meta.description}
                                  </span>
                                )}
                              </div>
                            </label>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default PluginAccessStep
