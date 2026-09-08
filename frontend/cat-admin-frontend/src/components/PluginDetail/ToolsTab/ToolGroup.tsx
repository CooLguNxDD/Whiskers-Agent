import { useState } from "react"
import type { ToolPermission } from "@/api/tools"
import type { LlmPoolEntry } from "@/api/config"
import { BatchToolActionBar } from "./components/BatchToolActionBar"
import { ToolItem } from "./components/ToolItem"
import {
  permModePatch,
  aggregateState,
  aggregatePermMode,
} from "@/lib/toolState"
import type { UnifiedState, PermMode, GroupedToolsBucket } from "@/lib/toolState"

interface ToolGroupProps {
  bucket: GroupedToolsBucket
  embeddingEntries: LlmPoolEntry[]
  ragEnabled: boolean
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
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="ct-chev"
      style={{
        transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
        transition: "transform 150ms ease",
      }}
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  )
}

/**
 * Collapsible category grouping of plugin tools with bulk state/permission apply.
 */
export function ToolGroup({
  bucket,
  embeddingEntries,
  ragEnabled,
  onSetToolState,
  onSetToolPermission,
  onSetToolEmbeddingModel,
  onSetToolsBatch,
}: ToolGroupProps) {
  const [groupExpanded, setGroupExpanded] = useState(true)
  const [subgroupsExpanded, setSubgroupsExpanded] = useState<Record<string, boolean>>({
    read: true,
    write: true,
  })

  const groupState = aggregateState(bucket.tools)
  const groupPermMode = aggregatePermMode(bucket.tools)

  const handleGroupStateChange = (nextState: UnifiedState) => {
    onSetToolsBatch(bucket.tools.map((tool) => tool.name), { state: nextState })
  }

  const handleGroupPermModeChange = (nextMode: PermMode) => {
    const patch = permModePatch(nextMode)
    if (patch) {
      onSetToolsBatch(bucket.tools.map((tool) => tool.name), { permission: patch })
    }
  }

  const toggleSubgroup = (access: string) => {
    setSubgroupsExpanded((prev) => ({
      ...prev,
      [access]: !prev[access],
    }))
  }

  const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

  return (
    <div className="ct-toolgroup">
      {/* Category Header */}
      <div
        role="button"
        tabIndex={0}
        className={`ct-toolgroup-head ${groupExpanded ? "is-expanded" : ""}`}
        aria-expanded={groupExpanded}
        onClick={() => setGroupExpanded(!groupExpanded)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            setGroupExpanded(!groupExpanded)
          }
        }}
      >
        <ChevronIcon expanded={groupExpanded} />
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--fg-default)",
            flex: 1,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {bucket.group}
          <span style={{ color: "var(--fg-subtle)", fontWeight: 400, fontSize: 11 }}>
            ({bucket.tools.length})
          </span>
        </span>

        {/* Bulk tools control at category level */}
        <BatchToolActionBar
          stateValue={groupState === "mixed" || groupPermMode === "mixed" || groupPermMode === "custom" ? "mixed" : groupState}
          permModeValue={groupPermMode}
          onStateChange={handleGroupStateChange}
          onPermModeChange={handleGroupPermModeChange}
        />
      </div>

      {/* Category Body */}
      {groupExpanded && (
        <div className="ct-toolgroup-body" style={{ padding: "8px 16px" }}>
          {bucket.subgroups.map((subgroup) => {
            const subExpanded = !!subgroupsExpanded[subgroup.access]
            const subState = aggregateState(subgroup.tools)
            const subPermMode = aggregatePermMode(subgroup.tools)

            const handleSubStateChange = (nextState: UnifiedState) => {
              onSetToolsBatch(subgroup.tools.map((tool) => tool.name), { state: nextState })
            }

            const handleSubPermModeChange = (nextMode: PermMode) => {
              const patch = permModePatch(nextMode)
              if (patch) {
                onSetToolsBatch(subgroup.tools.map((tool) => tool.name), { permission: patch })
              }
            }

            return (
              <div key={subgroup.access} className="ct-toolgroup-sub">
                {/* Subgroup Header */}
                <div
                  role="button"
                  tabIndex={0}
                  className={`ct-toolgroup-sub-head ${subExpanded ? "is-expanded" : ""}`}
                  aria-expanded={subExpanded}
                  onClick={() => toggleSubgroup(subgroup.access)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      toggleSubgroup(subgroup.access)
                    }
                  }}
                >
                  <ChevronIcon expanded={subExpanded} />
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      fontWeight: 600,
                      textTransform: "uppercase",
                      color: "var(--fg-muted)",
                      flex: 1,
                      display: "flex",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    {capitalize(subgroup.access)}
                    <span style={{ color: "var(--fg-subtle)", fontWeight: 400, fontSize: 10 }}>
                      ({subgroup.tools.length})
                    </span>
                  </span>

                  {/* Bulk tools control at subgroup level */}
                  <BatchToolActionBar
                    stateValue={subState === "mixed" || subPermMode === "mixed" || subPermMode === "custom" ? "mixed" : subState}
                    permModeValue={subPermMode}
                    onStateChange={handleSubStateChange}
                    onPermModeChange={handleSubPermModeChange}
                  />
                </div>

                {/* Subgroup Body */}
                {subExpanded && (
                  <div className="ct-toolgroup-sub-body" style={{ padding: "4px 12px" }}>
                    {subgroup.tools.map((tool) => (
                      <ToolItem
                        key={tool.name}
                        tool={tool}
                        ragEnabled={ragEnabled}
                        embeddingEntries={embeddingEntries}
                        onSetToolState={onSetToolState}
                        onSetToolPermission={onSetToolPermission}
                        onSetToolEmbeddingModel={onSetToolEmbeddingModel}
                      />
                    ))}
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
