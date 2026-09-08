/**
 * ToolsTab Component
 *
 * Lists available MCP tools registered by a specific plugin, allowing individual/group state and permission controls.
 */
import type { FC } from "react"
import { cn } from "@/lib/utils"
import type { PluginTool } from "@/api/plugins"
import type { LlmPoolEntry } from "@/api/config"
import type { ToolPermission } from "@/api/tools"
import { groupTools } from "@/lib/toolState"
import type { UnifiedState } from "@/lib/toolState"
import { ToolGroup } from "./ToolGroup"

export interface ToolsTabProps {
  tools: PluginTool[]
  isPending: boolean
  ragEnabled: boolean
  embeddingEntries: LlmPoolEntry[]
  onSetToolState: (toolName: string, state: UnifiedState) => void
  onSetToolPermission: (toolName: string, permission: Partial<ToolPermission>) => void
  onSetToolEmbeddingModel: (toolName: string, modelId: string | null) => void
  onSetToolsBatch: (
    toolNames: string[],
    patch: {
      state?: UnifiedState
      permission?: Partial<ToolPermission>
    }
  ) => void
  className?: string
}

/** Renders the list of MCP tools associated with a plugin, grouped by category and access tier. */
const ToolsTab: FC<ToolsTabProps> = ({
  tools,
  isPending,
  ragEnabled,
  embeddingEntries,
  onSetToolState,
  onSetToolPermission,
  onSetToolEmbeddingModel,
  onSetToolsBatch,
  className,
}) => {
  const total = tools.length
  // Exposed/Live tools are enabled and not hidden
  const liveCount = tools.filter((t) => t.is_enabled && !t.is_hidden).length

  const grouped = groupTools(tools)

  return (
    <div className={cn("ct-panel", className)}>
      <div className="ct-panel-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="ct-panel-title" style={{ flex: 1 }}>Tools</span>
        {!isPending && total > 0 && (
          <span className="ct-pill is-ok" style={{ fontFamily: "var(--font-mono)" }}>
            {liveCount}/{total} live
          </span>
        )}
      </div>
      <div className="ct-panel-body">
        {isPending && (
          <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
            Loading…
          </div>
        )}

        {!isPending && total === 0 && (
          <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>
            no tools registered
          </div>
        )}

        {!isPending && total > 0 && (
          <div className="ct-tools-list" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {grouped.map((bucket) => (
              <ToolGroup
                key={bucket.group}
                bucket={bucket}
                embeddingEntries={embeddingEntries}
                ragEnabled={ragEnabled}
                onSetToolState={onSetToolState}
                onSetToolPermission={onSetToolPermission}
                onSetToolEmbeddingModel={onSetToolEmbeddingModel}
                onSetToolsBatch={onSetToolsBatch}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default ToolsTab
