/**
 * PluginDetail Component
 *
 * Displays exhaustive information about a specific plugin, including metadata, health status,
 * registered tools, and external OAuth management.
 */

import { useState, type FC } from "react"
import { useNavigate } from "@tanstack/react-router"
import {
  usePluginDetailQuery,
  useTogglePluginMutation,
  usePluginHealthQuery,
  usePluginToolsQuery,
} from "@/hooks/usePlugins"
import { useSetToolStateMutation, useSetToolEmbeddingModelMutation, useSetToolPermissionMutation, useBatchToolStateMutation } from "@/hooks/useTools"
import { useLlmConfigQuery, useLlmPoolQuery } from "@/hooks/useConfig"
import AppShell from "@/components/shell/AppShell"
import { Bolt, ArrowLeft } from "@/components/shell/Icons"

import OverviewTab from "./OverviewTab"
import ToolsTab from "./ToolsTab"
import LogsTab from "./LogsTab"
import ConfigTab from "./ConfigTab"
import SkillsTab from "./SkillsTab"
import { CatalogActionsPanel } from "@/components/inference/CatalogActionsPanel"
import { isPluginConnected } from "@/lib/pluginAuth"
import RevokeConfirmModal from "@/components/ConnectModal/RevokeConfirmModal"

export interface PluginDetailProps {
  pluginId: string
}

type Tab = "overview" | "tools" | "actions" | "logs" | "config" | "skills"

/**
 * Renders the detail view for a plugin with tab-based navigation.
 */
/**
 * Renders the detail view for a plugin with tab-based navigation.
 */
const PluginDetail: FC<PluginDetailProps> = ({ pluginId }) => {
  const navigate = useNavigate()
  const { data: plugin, isPending } = usePluginDetailQuery(pluginId)
  const { mutate: togglePlugin, isPending: isToggling } = useTogglePluginMutation()
  const { data: healthData } = usePluginHealthQuery(pluginId)
  const [tab, setTab] = useState<Tab>("overview")
  const [isRevokeOpen, setIsRevokeOpen] = useState(false)

  const { data: llmConfig } = useLlmConfigQuery()
  const ragEnabled = llmConfig?.rag_enabled ?? true // default show while loading

  const { data: toolsData, isPending: isToolsPending } = usePluginToolsQuery(pluginId)
  const { mutate: setToolState } = useSetToolStateMutation(pluginId)
  const { mutate: setToolsBatch } = useBatchToolStateMutation(pluginId)
  const { data: poolData } = useLlmPoolQuery()
  const embeddingEntries = poolData?.entries.filter(e => e.kind === "embedding") || []
  const { mutate: setToolEmbeddingModel } = useSetToolEmbeddingModelMutation(pluginId)
  const { mutate: setToolPermission } = useSetToolPermissionMutation(pluginId)

  // Tools tab is always available — MCP exposure is independent of RAG.
  // skills tab allows add/mod/delete of DB-persisted workflow docs (seeded on initial load).
  const visibleTabs: Tab[] = ["overview", "tools", "actions", "logs", "config", "skills"]

  if (isPending) {
    return (
      <AppShell active="plugins">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {[0, 1].map((i) => (
            <div key={i} style={{ height: 80, background: "var(--card)", borderRadius: "var(--radius)", border: "1px solid var(--hairline)", opacity: 0.5 }} />
          ))}
        </div>
      </AppShell>
    )
  }

  if (!plugin) {
    return (
      <AppShell active="plugins">
        <div className="ct-detail-crumbs">
          <button className="ct-btn-ghost" onClick={() => void navigate({ to: "/plugins" })}>
            <ArrowLeft width="13" height="13" /> Plugins
          </button>
          <span className="sep">/</span>
          <span>Not found</span>
        </div>
        <div style={{ padding: "60px 0", textAlign: "center", color: "var(--fg-muted)" }}>
          Plugin not found.
        </div>
      </AppShell>
    )
  }

  const oauthProviders = plugin.external_oauth_providers ?? []

  return (
    <AppShell active="plugins">
      {/* breadcrumbs */}
      <div className="ct-detail-crumbs">
        <button
          className="ct-btn-ghost"
          style={{ padding: "5px 10px", fontSize: 12 }}
          onClick={() => void navigate({ to: "/plugins" })}
        >
          <ArrowLeft width="13" height="13" /> Plugins
        </button>
        <span className="sep">/</span>
        <span className="cur">{plugin.name}</span>
      </div>

      {/* header card */}
      <div className="ct-detail-head">
        <div className="ct-plugin-ico">
          <Bolt width="26" height="26" />
        </div>
        <div>
          <div className="ct-detail-name">
            {plugin.name}
            <span className={"ct-tier " + (plugin.tier === "pro" ? "is-pro" : "is-lite")}>{plugin.tier}</span>
            <span className={"ct-pill " + (plugin.enabled ? "is-ok" : "")}>
              <span className={"ct-dot " + (plugin.enabled ? "is-ok" : "is-off")} />
              {plugin.enabled ? "running" : "stopped"}
            </span>
          </div>
          <div className="ct-detail-desc">{plugin.description}</div>
        </div>
        <div className="ct-detail-actions">
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--fg-muted)" }}>v{plugin.version}</span>
          {isPluginConnected(plugin, healthData) ? (
            <button
              className="ct-btn-danger"
              style={{ fontSize: 12, padding: "6px 12px" }}
              onClick={() => setIsRevokeOpen(true)}
            >
              Revoke
            </button>
          ) : (
            <button
              className="ct-btn-pink"
              style={{ fontSize: 12, padding: "6px 12px" }}
              onClick={() =>
                void navigate({
                  to: "/connect",
                  search: { plugin_id: plugin.id, provider: oauthProviders[0] ?? "whiskers_core", state: "", oauth_success: "" },
                })
              }
            >
              Connect
            </button>
          )}
          <button
            className={"ct-switch " + (plugin.enabled ? "is-on" : "")}
            role="switch"
            aria-checked={plugin.enabled}
            onClick={() => !isToggling && togglePlugin({ id: plugin.id, enabled: !plugin.enabled })}
            aria-label="Toggle plugin"
          />

        </div>
      </div>

      {/* tab bar */}
      <div className="ct-section" style={{ marginBottom: 0 }}>
        <div className="ct-tabs">
          {visibleTabs.map((t) => (
            <button key={t} className={"ct-tab" + (tab === t ? " is-active" : "")} onClick={() => setTab(t)}>
              {(t as string) === "oauth" ? "auth" : t}
            </button>
          ))}
        </div>

        {/* tab content */}
        <div style={{ padding: "20px 22px" }}>
          {tab === "overview" && <OverviewTab plugin={plugin} healthData={healthData} onTabChange={setTab} />}
          {tab === "tools" && (
            <ToolsTab
              tools={toolsData?.tools ?? []}
              isPending={isToolsPending}
              ragEnabled={ragEnabled}
              embeddingEntries={embeddingEntries}
              onSetToolState={(toolName, state) => setToolState({ toolName, state })}
              onSetToolPermission={(toolName, permission) => setToolPermission({ toolName, permission })}
              onSetToolEmbeddingModel={(toolName, modelId) => setToolEmbeddingModel({ toolName, modelId })}
              onSetToolsBatch={(toolNames, patch) => setToolsBatch({ toolNames, ...patch })}
            />
          )}
          {tab === "actions" && <CatalogActionsPanel pluginId={pluginId} />}
          {tab === "logs" && <LogsTab pluginId={pluginId} />}
          {tab === "config" && <ConfigTab pluginId={pluginId} />}
          {tab === "skills" && <SkillsTab pluginId={pluginId} />}
        </div>
      </div>
      <div style={{ height: 12 }} />

      <RevokeConfirmModal
        isOpen={isRevokeOpen}
        onOpenChange={setIsRevokeOpen}
        plugin={plugin}
        healthData={healthData}
      />
    </AppShell>
  )
}

/**
 * PluginDetail Component.
 * Renders the UI and handles state for the PluginDetail feature.
 */
export default PluginDetail

