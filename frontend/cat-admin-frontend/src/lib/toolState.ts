import type { PluginTool } from "@/api/plugins"
import type { ToolPermission } from "@/api/tools"

export type UnifiedState = "enabled" | "hidden" | "disabled"
export type UnifiedStateOrMixed = UnifiedState | "mixed"
export type PermMode = "auto" | "approval" | "custom"
export type PermModeOrMixed = PermMode | "mixed"

/**
 * Derives the 3-stage state from is_enabled and is_hidden, with disabled winning.
 */
export function deriveState(tool: PluginTool): UnifiedState {
  if (!tool.is_enabled) {
    return "disabled"
  }
  return tool.is_hidden ? "hidden" : "enabled"
}

/**
 * Returns the canonical pair of enabled/hidden booleans for a unified state.
 */
export function statePatch(state: UnifiedState): { enable: boolean; hide: boolean } {
  switch (state) {
    case "enabled":
      return { enable: true, hide: false }
    case "hidden":
      return { enable: true, hide: true }
    case "disabled":
    default:
      return { enable: false, hide: false }
  }
}

/**
 * Derives the permission mode based on the current policy values.
 */
export function derivePermMode(perm: ToolPermission | null | undefined): PermMode {
  if (!perm) {
    return "approval"
  }
  const { allow_read = true, allow_write = true, require_confirmation = true } = perm
  if (allow_read === true && allow_write === true && require_confirmation === false) {
    return "auto"
  }
  if (allow_read === true && allow_write === true && require_confirmation === true) {
    return "approval"
  }
  return "custom"
}

/**
 * Returns the partial permission properties to patch for the specified mode.
 * Returns null for custom mode as it reveals custom R/W/C controls instead of auto-patching.
 */
export function permModePatch(mode: PermMode): Partial<ToolPermission> | null {
  switch (mode) {
    case "auto":
      return { allow_read: true, allow_write: true, require_confirmation: false }
    case "approval":
      return { allow_read: true, allow_write: true, require_confirmation: true }
    case "custom":
    default:
      return null
  }
}

export interface GroupSubgroup {
  access: "read" | "write"
  tools: PluginTool[]
}

export interface GroupedToolsBucket {
  group: string
  subgroups: GroupSubgroup[]
  tools: PluginTool[]
}

/**
 * Groups tools into categories (outer) and read/write access (inner), stable sorted.
 */
export function groupTools(tools: PluginTool[]): GroupedToolsBucket[] {
  const groupsMap = new Map<string, PluginTool[]>()
  for (const t of tools) {
    const groupName = t.group || "general"
    if (!groupsMap.has(groupName)) {
      groupsMap.set(groupName, [])
    }
    groupsMap.get(groupName)!.push(t)
  }

  const result = Array.from(groupsMap.entries()).map(([groupName, groupTools]) => {
    const sortedTools = [...groupTools].sort((a, b) => a.name.localeCompare(b.name))
    const readTools = sortedTools.filter((t) => t.access !== "write")
    const writeTools = sortedTools.filter((t) => t.access === "write")

    const subgroups: GroupSubgroup[] = []
    if (readTools.length > 0) {
      subgroups.push({ access: "read", tools: readTools })
    }
    if (writeTools.length > 0) {
      subgroups.push({ access: "write", tools: writeTools })
    }
    subgroups.sort((a, b) => a.access.localeCompare(b.access))

    return {
      group: groupName,
      subgroups,
      tools: sortedTools,
    }
  })

  result.sort((a, b) => {
    if (a.group === "general" && b.group !== "general") return -1
    if (b.group === "general" && a.group !== "general") return 1
    return a.group.localeCompare(b.group)
  })

  return result
}

/**
 * Returns the single state representing all tools, or "mixed".
 */
export function aggregateState(tools: PluginTool[]): UnifiedStateOrMixed {
  if (tools.length === 0) return "disabled"
  const states = tools.map(deriveState)
  const unique = Array.from(new Set(states))
  return unique.length === 1 ? unique[0] : "mixed"
}

/**
 * Returns the single permission mode representing all tools, or "mixed".
 */
export function aggregatePermMode(tools: PluginTool[]): PermModeOrMixed {
  if (tools.length === 0) return "approval"
  const modes = tools.map((t) => derivePermMode(t.permission))
  const unique = Array.from(new Set(modes))
  return unique.length === 1 ? unique[0] : "mixed"
}
