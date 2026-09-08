/**
 * useScopeEditor — local scope-toggle state machine (FSM) for the scope configurator view.
 *
 * Pure local state, no server calls. Initialized once per mount — the route unmounts
 * this when going back to the list, so re-initialization on key change is handled by
 * natural React lifecycle.
 */

import { useCallback, useState } from "react"
import type { Plugin } from "@/types/plugin"
import type { PluginScopeContribution, CoreScopeContribution } from "@/api/apiKeys"

export type ToolsQuery = { data?: { tools?: Array<{ group?: string; access?: string }> }; isPending?: boolean }

/**
 * Scope editor preset state configurations.
 */
export const ScopeEditorState = {
  ALL: "all",
  NONE: "none",
  TERMINAL: "terminal",
  CORE: "core",
  CUSTOM: "custom",
} as const

export type ScopeEditorState = (typeof ScopeEditorState)[keyof typeof ScopeEditorState]

const STATE_MAP: Record<
  ScopeEditorState,
  {
    getScopes: (custom: string[], all: string[]) => string[]
    isScopeSelected: (scope: string, custom: string[]) => boolean
    isPluginWideSelected: (pluginId: string, custom: string[]) => boolean
  }
> = {
  [ScopeEditorState.ALL]: {
    getScopes: (_custom, all) => all,
    isScopeSelected: () => true,
    isPluginWideSelected: () => true,
  },
  [ScopeEditorState.NONE]: {
    getScopes: () => [],
    isScopeSelected: () => false,
    isPluginWideSelected: () => false,
  },
  [ScopeEditorState.TERMINAL]: {
    // core_047_scope_cutover rewrites terminal:use/terminal:host to these
    // level-1 grammar tokens; keep both the emitted preset and the detector
    // below in sync with core/scope_management/legacy_map.py.
    getScopes: () => ["core:terminal:write", "core:terminal:read"],
    isScopeSelected: (scope) => scope === "core:terminal:write" || scope === "core:terminal:read",
    isPluginWideSelected: () => false,
  },
  [ScopeEditorState.CORE]: {
    getScopes: (custom) => custom,
    isScopeSelected: (scope, custom) => custom.includes(scope),
    isPluginWideSelected: () => false,
  },
  [ScopeEditorState.CUSTOM]: {
    getScopes: (custom) => custom,
    isScopeSelected: (scope, custom) => custom.includes(scope),
    isPluginWideSelected: (pluginId, custom) => custom.includes(`plugin:${pluginId}`),
  },
}

/**
 * Custom hook encapsulating the logic for editing and merging scope arrays.
 */
export function useScopeEditor(
  initialScopes: string[] | null,
  plugins: Plugin[],
  toolsQueries: ToolsQuery[],
  globalScopesList: string[],
  pluginScopesList: PluginScopeContribution[] = [],
  coreScopesList: CoreScopeContribution[] | string[] = []
) {
  const detectStateFromScopes = (scopes: string[] | null): { state: ScopeEditorState; custom: string[] } => {
    if (scopes === null) {
      return { state: ScopeEditorState.ALL, custom: [] }
    }
    if (scopes.length === 1 && (scopes[0] === "all" || scopes[0] === "*")) {
      return { state: ScopeEditorState.ALL, custom: [] }
    }
    if (scopes.length === 0) {
      return { state: ScopeEditorState.NONE, custom: [] }
    }
    if (
      scopes.length === 2 &&
      scopes.includes("core:terminal:write") &&
      scopes.includes("core:terminal:read")
    ) {
      return { state: ScopeEditorState.TERMINAL, custom: [] }
    }
    if (scopes.length > 0 && scopes.every((s) => s.startsWith("core:"))) {
      return { state: ScopeEditorState.CORE, custom: scopes }
    }
    return { state: ScopeEditorState.CUSTOM, custom: scopes }
  }

  const [editorState, setEditorState] = useState<ScopeEditorState>(() => detectStateFromScopes(initialScopes).state)
  const [allAccessSentinel, setAllAccessSentinel] = useState<null | ["all"]>(() =>
    initialScopes === null ? null : ["all"]
  )
  const [customScopes, setCustomScopes] = useState<string[]>(() => detectStateFromScopes(initialScopes).custom)

  const getAllAvailableScopes = useCallback((): string[] => {
    const safePluginScopesList = Array.isArray(pluginScopesList) ? pluginScopesList : []
    const pluginTokens = safePluginScopesList.map((ps) => ps.token)
    const filteredGlobal = globalScopesList.filter((s) => !pluginTokens.includes(s))
    const all: string[] = [...filteredGlobal]

    safePluginScopesList.forEach((ps) => {
      if (!all.includes(ps.token)) {
        all.push(ps.token)
      }
    })

    const safeCoreTokens = Array.isArray(coreScopesList)
      ? coreScopesList.map((c) => (typeof c === "string" ? c : c.token))
      : []
    safeCoreTokens.forEach((tok) => {
      if (!all.includes(tok)) {
        all.push(tok)
      }
    })

    // Bare plugin:<id> full-access toggle is a standard, registry-validated
    // token — synthesized here for UX convenience. group:<id>:<tag> chips
    // are NOT synthesized from live tool groups any more (that invented
    // ids `_validate_scopes_against_issuer` would reject at save with
    // scopes_exceed_issuer — a UI bug, not an escalation, but still dead
    // weight); the plugin-declared group tokens already come from
    // pluginScopesList above (the level-tagged vocabulary endpoint).
    plugins.forEach((p) => {
      if (!all.includes(`plugin:${p.id}`)) {
        all.push(`plugin:${p.id}`)
      }
    })
    return all
  }, [pluginScopesList, globalScopesList, coreScopesList, plugins])

  const getPluginGroups = (_pluginId: string, idx: number): string[] => {
    const tools = toolsQueries[idx]?.data?.tools ?? []
    return Array.from(new Set(tools.map((t) => t.group || "general")))
  }

  const selectedScopes = editorState === ScopeEditorState.ALL
    ? allAccessSentinel
    : STATE_MAP[editorState].getScopes(customScopes, [])

  /** True when UI represents full access (null legacy or ["all"] sentinel). */
  const isAllAccess = (): boolean => editorState === ScopeEditorState.ALL

  const isScopeSelected = (scope: string): boolean => {
    return STATE_MAP[editorState].isScopeSelected(scope, customScopes)
  }

  /** Mirror grammar.expand_implied for UI preview: returns scopes implied by a granted token. */
  const impliedFor = useCallback((token: string): string[] => {
    if (!token || typeof token !== "string") return []
    const out: string[] = []
    if (token.startsWith("core:")) {
      const parts = token.split(":")
      if (parts.length === 3 && parts[2] === "write") {
        out.push(`core:${parts[1]}:read`)
      }
    } else if (token.startsWith("plugin:")) {
      const parts = token.split(":")
      const pid = parts[1]
      if (parts.length === 2) {
        out.push(`plugin:${pid}:write`, `plugin:${pid}:read`)
        const safePluginScopes = Array.isArray(pluginScopesList) ? pluginScopesList : []
        safePluginScopes.forEach((ps) => {
          if (ps.plugin_id === pid && ps.token !== token) {
            out.push(ps.token)
          }
        })
      } else if (parts.length === 3 && parts[2] === "write") {
        out.push(`plugin:${pid}:read`)
      }
    }
    return out
  }, [pluginScopesList])

  const updateFsmState = (scopes: string[]) => {
    const { state: nextState, custom: nextCustom } = detectStateFromScopes(scopes)
    setEditorState(nextState)
    setCustomScopes(nextCustom)
    if (nextState === ScopeEditorState.ALL) {
      setAllAccessSentinel((prev) => prev ?? ["all"])
    }
  }

  const toggleScope = (scope: string) => {
    const currentActiveScopes = STATE_MAP[editorState].getScopes(customScopes, getAllAvailableScopes())
    let nextScopes: string[]
    if (currentActiveScopes.includes(scope)) {
      nextScopes = currentActiveScopes.filter((s) => s !== scope)
    } else {
      if (scope.startsWith("group:")) {
        const parts = scope.split(":")
        if (parts.length >= 3) {
          const pluginId = parts[1]
          const pluginWideScope = `plugin:${pluginId}`
          if (currentActiveScopes.includes(pluginWideScope)) {
            const filtered = currentActiveScopes.filter((s) => s !== pluginWideScope)
            nextScopes = [...filtered, scope]
            updateFsmState(nextScopes)
            return
          }
        }
      }
      nextScopes = [...currentActiveScopes, scope]
    }

    updateFsmState(nextScopes)
  }

  const isPluginWideSelected = (pluginId: string): boolean => {
    return STATE_MAP[editorState].isPluginWideSelected(pluginId, customScopes)
  }

  const togglePluginWide = (pluginId: string) => {
    const scope = `plugin:${pluginId}`
    const currentActiveScopes = STATE_MAP[editorState].getScopes(customScopes, getAllAvailableScopes())
    let nextScopes: string[]
    if (currentActiveScopes.includes(scope)) {
      nextScopes = currentActiveScopes.filter((s) => s !== scope)
    } else {
      const filtered = currentActiveScopes.filter((s) => !s.startsWith(`group:${pluginId}:`))
      nextScopes = [...filtered, scope]
    }

    updateFsmState(nextScopes)
  }

  const applyPreset = (preset: "all" | "none" | "terminal") => {
    const presetStateMap: Record<typeof preset, ScopeEditorState> = {
      all: ScopeEditorState.ALL,
      none: ScopeEditorState.NONE,
      terminal: ScopeEditorState.TERMINAL,
    }
    const nextState = presetStateMap[preset]
    setEditorState(nextState)
    if (preset === "all") {
      setAllAccessSentinel(["all"])
    }
    setCustomScopes([])
  }

  const applyCustomScopes = (scopes: string[] | null) => {
    const { state: nextState, custom: nextCustom } = detectStateFromScopes(scopes)
    setEditorState(nextState)
    if (scopes === null) {
      setAllAccessSentinel(null)
    } else if (nextState === ScopeEditorState.ALL) {
      setAllAccessSentinel(["all"])
    }
    setCustomScopes(nextCustom)
  }

  /** Normalize for API write: null (all-checked UI) → ["all"]. */
  const scopesForPersist = (): string[] => {
    if (editorState === ScopeEditorState.ALL) return ["all"]
    return STATE_MAP[editorState].getScopes(customScopes, [])
  }

  return {
    selectedScopes,
    editorState,
    getAllAvailableScopes,
    getPluginGroups,
    isScopeSelected,
    toggleScope,
    isPluginWideSelected,
    togglePluginWide,
    applyPreset,
    applyCustomScopes,
    scopesForPersist,
    isAllAccess,
    impliedFor,
  }
}

export type ScopeEditor = ReturnType<typeof useScopeEditor>
