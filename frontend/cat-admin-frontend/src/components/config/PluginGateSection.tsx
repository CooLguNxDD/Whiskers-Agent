/**
 * PluginGateSection — Level 3 Plugin Gate Ceilings configuration.
 * Two-pane editor: plugin list on the left, gate ceiling editor on the right.
 */

import { useState, type FC } from "react"
import {
  usePluginGatesQuery,
  useSavePluginGateMutation,
  useDeletePluginGateMutation,
} from "@/hooks/useConfig"
import { usePluginsQuery } from "@/hooks/usePlugins"
import { useScopeVocabularyQuery } from "@/hooks/useApiKeys"
import type { PluginGateSpec } from "@/api/config"
import { getErrorMessage } from "@/utils/errors"
import { ShieldAlert, RefreshCw, Layers } from "lucide-react"

/**
 * CsvInput — comma-separated-list input backed by local text state.
 *
 * Binding `value` straight to `array.join(", ")` and splitting on every
 * keystroke collapses a just-typed ", " into "," and jumps the caret to the
 * end, making it impossible to type a second item. This buffers raw text
 * locally and only commits the parsed array on blur. Re-syncing to genuine
 * external changes (plugin switch, gate reload) is the caller's job: pass a
 * `key` that changes with the selected plugin so React remounts (and thus
 * resets local text) instead of leaving stale text from a prior selection.
 */
const CsvInput: FC<{
  id: string
  placeholder?: string
  value: string[]
  onCommit: (list: string[]) => void
}> = ({ id, placeholder, value, onCommit }) => {
  const [text, setText] = useState(() => value.join(", "))

  return (
    <input
      id={id}
      className="ct-input"
      placeholder={placeholder}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        const list = text
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
        onCommit(list)
      }}
    />
  )
}

/** Two-pane Level-3 gate-ceiling editor: plugin list, then the selected plugin's allow/deny gate spec. See file header. */
export const PluginGateSection: FC = () => {
  const { data: gatesData, isPending: isGatesPending } = usePluginGatesQuery()
  const { data: pluginsData, isPending: isPluginsPending } = usePluginsQuery(true)
  const { data: vocabData } = useScopeVocabularyQuery(true)

  const saveGate = useSavePluginGateMutation()
  const deleteGate = useDeletePluginGateMutation()

  const [selectedPluginId, setSelectedPluginId] = useState<string | null>(null)
  const [draft, setDraft] = useState<PluginGateSpec | null>(null)

  const coreScopes = (vocabData?.core_scopes ?? []).map((s) => s.token)
  const defaultCoreScopes = [
    "core:graph:read",
    "core:graph:write",
    "core:terminal:read",
    "core:terminal:write",
    "core:config:read",
    "core:config:write",
    "core:apikey:read",
    "core:apikey:write",
  ]
  const availableCoreOptions = Array.from(new Set([...coreScopes, ...defaultCoreScopes])).sort()

  if (isGatesPending || isPluginsPending) {
    return (
      <div className="ct-form-section">
        <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
          Loading plugin gates…
        </div>
      </div>
    )
  }

  const gateMap = new Map<string, PluginGateSpec>()
  ;(gatesData?.gates ?? []).forEach((g) => gateMap.set(g.plugin_id, g))

  // Combine installed plugins and any gated plugins
  const allPluginIds = Array.from(
    new Set([
      ...(pluginsData?.plugins ?? []).map((p) => p.id),
      ...(gatesData?.gates ?? []).map((g) => g.plugin_id),
    ])
  ).sort()

  function selectPlugin(pluginId: string) {
    setSelectedPluginId(pluginId)
    const existing = gateMap.get(pluginId)
    if (existing) {
      setDraft({ ...existing })
    } else {
      // Default blank gate for unconfigured plugin
      setDraft({
        plugin_id: pluginId,
        core: ["core:graph:read"],
        access: "read",
        operations: { allow: [], deny: [] },
        endpoints: [],
        owner: "db",
        source: undefined,
      })
    }
    saveGate.reset()
    deleteGate.reset()
  }

  const selected = selectedPluginId ? (draft ?? gateMap.get(selectedPluginId) ?? null) : null

  function updateDraft(patch: Partial<PluginGateSpec>) {
    if (saveGate.isError) saveGate.reset()
    setDraft((d) => (d ? { ...d, ...patch } : d))
  }

  function toggleCoreToken(token: string) {
    if (!draft) return
    const current = draft.core ?? []
    const next = current.includes(token)
      ? current.filter((t) => t !== token)
      : [...current, token]
    updateDraft({ core: next })
  }

  function handleSave() {
    if (!draft || !selectedPluginId) return
    saveGate.mutate(
      {
        pluginId: selectedPluginId,
        spec: {
          core: draft.core,
          access: draft.access,
          operations: draft.operations,
          endpoints: draft.endpoints,
        },
      },
      {
        onSuccess: (res) => {
          if (res.gate) setDraft(res.gate)
        },
      }
    )
  }

  function handleReset() {
    if (!selectedPluginId) return
    deleteGate.mutate(selectedPluginId, {
      onSuccess: (res) => {
        if (res.gate) {
          setDraft(res.gate)
        } else {
          setDraft({
            plugin_id: selectedPluginId,
            core: ["core:graph:read"],
            access: "read",
            operations: { allow: [], deny: [] },
            endpoints: [],
            owner: "db",
            source: undefined,
          })
        }
      },
    })
  }

  return (
    <div className="ct-form-section">
      <h3>Plugin Gate Ceilings</h3>
      <p className="sub">
        Level 3 security ceilings restricting what platform capabilities and operations each plugin
        may access. DB overrides take precedence over manifest defaults.
      </p>

      <div style={{ display: "flex", gap: 20, marginTop: 20 }}>
        {/* Left Pane: Plugin List */}
        <div style={{ minWidth: 220 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Plugins</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {allPluginIds.map((pluginId) => {
              const gate = gateMap.get(pluginId)
              const source = gate?.source
              const hasCeiling = !!gate

              return (
                <button
                  key={pluginId}
                  type="button"
                  className="ct-btn-ghost"
                  style={{
                    justifyContent: "space-between",
                    display: "flex",
                    fontWeight: selectedPluginId === pluginId ? 700 : 400,
                  }}
                  onClick={() => selectPlugin(pluginId)}
                  aria-current={selectedPluginId === pluginId ? "true" : undefined}
                >
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{pluginId}</span>
                  <span
                    style={{
                      fontSize: 10,
                      padding: "1px 6px",
                      borderRadius: 8,
                      background:
                        source === "db"
                          ? "var(--neon-soft)"
                          : hasCeiling
                          ? "var(--bg-input)"
                          : "color-mix(in oklch, var(--fg) 5%, transparent)",
                      color:
                        source === "db"
                          ? "var(--neon)"
                          : hasCeiling
                          ? "var(--fg-subtle)"
                          : "var(--fg-subtle)",
                    }}
                  >
                    {source === "db" ? "db" : hasCeiling ? "manifest" : "no ceiling"}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Right Pane: Gate Ceiling Editor */}
        <div style={{ flex: 1 }}>
          {!selected ? (
            <div style={{ color: "var(--fg-subtle)", fontSize: 12 }}>
              Select a plugin to inspect or edit its gate ceiling.
            </div>
          ) : (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: 14, fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                  {selected.plugin_id}
                </div>
                {selected.source === "db" && (
                  <button
                    type="button"
                    className="ct-btn-ghost"
                    onClick={handleReset}
                    disabled={deleteGate.isPending}
                  >
                    Reset to manifest
                  </button>
                )}
              </div>

              {/* Core Platform Scope Multi-select */}
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                  Allowed Core Platform Scopes (<code>core[]</code>)
                </div>
                <p className="sub" style={{ marginTop: 0 }}>
                  Select the core platform scopes this plugin is permitted to reach. An empty list means no core reach.
                </p>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                    gap: 8,
                    marginTop: 8,
                  }}
                >
                  {availableCoreOptions.map((token) => {
                    const isChecked = selected.core?.includes(token) ?? false
                    return (
                      <label
                        key={token}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          fontSize: 11,
                          fontFamily: "var(--font-mono)",
                          padding: "6px 10px",
                          borderRadius: "var(--radius)",
                          border: "1px solid var(--border)",
                          background: isChecked
                            ? "color-mix(in oklch, var(--amber) 10%, transparent)"
                            : "transparent",
                          cursor: "pointer",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => toggleCoreToken(token)}
                          className="accent-[var(--amber)]"
                        />
                        <span>{token}</span>
                      </label>
                    )
                  })}
                </div>
              </div>

              {/* Access Class */}
              <div style={{ marginTop: 16 }}>
                <div className="ct-field">
                  <label htmlFor="gate-access">Access Class</label>
                  <select
                    id="gate-access"
                    className="ct-input"
                    style={{ background: "var(--bg-input)" }}
                    value={selected.access || "read"}
                    onChange={(e) =>
                      updateDraft({ access: e.target.value as "read" | "write" })
                    }
                  >
                    <option value="read">read</option>
                    <option value="write">write</option>
                  </select>
                </div>
              </div>

              {/* Operations Allow / Deny Lists */}
              <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="ct-field">
                  <label htmlFor="gate-op-allow">Operations Allow (comma-separated)</label>
                  <CsvInput
                    key={`${selectedPluginId}-op-allow`}
                    id="gate-op-allow"
                    placeholder="e.g. search, query"
                    value={selected.operations?.allow ?? []}
                    onCommit={(list) =>
                      updateDraft({
                        operations: {
                          ...(selected.operations ?? {}),
                          allow: list,
                        },
                      })
                    }
                  />
                </div>

                <div className="ct-field">
                  <label htmlFor="gate-op-deny">Operations Deny (comma-separated)</label>
                  <CsvInput
                    key={`${selectedPluginId}-op-deny`}
                    id="gate-op-deny"
                    placeholder="e.g. bake_portfolio_for_job, run_raw"
                    value={selected.operations?.deny ?? []}
                    onCommit={(list) =>
                      updateDraft({
                        operations: {
                          ...(selected.operations ?? {}),
                          deny: list,
                        },
                      })
                    }
                  />
                </div>
              </div>

              {/* Endpoints Glob List */}
              <div style={{ marginTop: 16 }}>
                <div className="ct-field">
                  <label htmlFor="gate-endpoints">Endpoints Globs (comma-separated)</label>
                  <CsvInput
                    key={`${selectedPluginId}-endpoints`}
                    id="gate-endpoints"
                    placeholder="e.g. /api/analytics/*, /api/ws/*"
                    value={selected.endpoints ?? []}
                    onCommit={(list) => updateDraft({ endpoints: list })}
                  />
                </div>
              </div>

              {/* Error and Success states */}
              {saveGate.isError && (
                <div style={{ color: "var(--danger)", fontSize: 12, fontWeight: 600, marginTop: 12 }}>
                  {getErrorMessage(saveGate.error, "Failed to save plugin gate spec.")}
                </div>
              )}
              {saveGate.isSuccess && (
                <div style={{ color: "var(--neon)", fontSize: 12, fontWeight: 600, marginTop: 12 }}>
                  Gate ceiling saved successfully.
                </div>
              )}

              <button
                type="button"
                className="ct-btn-primary"
                style={{ marginTop: 16 }}
                onClick={handleSave}
                disabled={saveGate.isPending}
              >
                {saveGate.isPending ? "Saving..." : "Save Gate Ceiling"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default PluginGateSection
