import type { FC } from "react"
import { StateToggle } from "../StateToggle"
import { PermissionSelect } from "../PermissionSelect"
import type { PluginTool } from "@/api/plugins"
import type { ToolPermission } from "@/api/tools"
import type { UnifiedState } from "@/lib/toolState"
import { deriveState, derivePermMode, permModePatch } from "@/lib/toolState"
import type { LlmPoolEntry } from "@/api/config"

const SEARCH_PREFIX = "semantic_search_"
const UPSERT_RE = /^upsert_.+_embedding$/

/**
 * True for the two halves of a semantic tool pair, which carry an embedding-model
 * control. Matched by shape rather than a hardcoded list, so a plugin shipping its
 * own `upsert_<entity>_embedding` / `semantic_search_<entity>s` pair gets it too.
 */
function isSemanticTool(name: string): boolean {
  return name.startsWith(SEARCH_PREFIX) || UPSERT_RE.test(name)
}

/**
 * Name the producer tool a search tool inherits its embedding model from:
 * `semantic_search_notes` -> `upsert_note_embedding`. The trailing plural is
 * dropped because search tools are named for the collection and producers for
 * the single entity.
 */
function producerToolFor(searchToolName: string): string {
  const entity = searchToolName.slice(SEARCH_PREFIX.length).replace(/s$/, "")
  return `upsert_${entity}_embedding`
}

export interface ToolItemProps {
  tool: PluginTool
  ragEnabled: boolean
  embeddingEntries: LlmPoolEntry[]
  onSetToolState: (toolName: string, state: UnifiedState) => void
  onSetToolPermission: (toolName: string, permission: Partial<ToolPermission>) => void
  onSetToolEmbeddingModel: (toolName: string, modelId: string | null) => void
}

/**
 * Represents a single tool item with its configuration controls.
 */
export const ToolItem: FC<ToolItemProps> = ({
  tool,
  ragEnabled,
  embeddingEntries,
  onSetToolState,
  onSetToolPermission,
  onSetToolEmbeddingModel,
}) => {
  const tState = deriveState(tool)
  const tPermMode = derivePermMode(tool.permission)

  let stateLabel = "LIVE"
  let pillClass = "is-ok"
  if (tState === "disabled") {
    stateLabel = "OFF"
    pillClass = "is-disabled"
  } else if (tState === "hidden") {
    stateLabel = "HIDDEN"
    pillClass = "is-hidden"
  }

  return (
    <div
      className="ct-tool"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 0",
        borderBottom: "1px solid var(--hairline)",
      }}
    >
      <div style={{ flex: 1, minWidth: 0, paddingRight: 16 }}>
        <div
          className="ct-tool-name"
          style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 500 }}
        >
          {tool.name}
          <span
            className={`ct-pill ${pillClass}`}
            style={{
              fontSize: 8,
              padding: "1px 4px",
              textTransform: "uppercase",
              fontWeight: 600,
            }}
          >
            {stateLabel}
          </span>
        </div>
        <div
          className="ct-tool-desc"
          style={{
            fontSize: 11,
            color: "var(--fg-subtle)",
            marginTop: 2,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {tool.description || "No description"}
        </div>

        {ragEnabled && isSemanticTool(tool.name) && (
            <div
              style={{
                marginTop: 6,
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 10.5,
              }}
            >
              <span style={{ color: "var(--fg-subtle)" }}>Embedding model:</span>
              {tool.name.startsWith(SEARCH_PREFIX) ? (
                <span style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>
                  Managed by {producerToolFor(tool.name)}
                </span>
              ) : (
                <select
                  className="ct-input"
                  style={{
                    width: "auto",
                    minWidth: 160,
                    padding: "1px 4px",
                    height: 20,
                    fontSize: 10,
                    background: "var(--bg-input)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    color: "var(--fg-default)",
                  }}
                  value={tool.embedding_model || "default"}
                  onChange={(e) => {
                    const val = e.target.value
                    onSetToolEmbeddingModel(tool.name, val === "default" ? null : val)
                  }}
                >
                  <option value="default">Default (Env Fallback)</option>
                  {embeddingEntries.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.provider}: {entry.model}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexShrink: 0,
        }}
      >
        <StateToggle
          value={tState}
          onChange={(nextState) => onSetToolState(tool.name, nextState)}
        />
        <PermissionSelect
          mode={tPermMode}
          perm={tool.permission}
          onModeChange={(nextMode) => {
            const patch = permModePatch(nextMode)
            if (patch) {
              onSetToolPermission(tool.name, patch)
            }
          }}
          onPermPatch={(patch) => onSetToolPermission(tool.name, patch)}
        />
      </div>
    </div>
  )
}
