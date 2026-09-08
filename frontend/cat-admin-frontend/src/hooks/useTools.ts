/**
 * useTools Hooks
 *
 * Hook for toggling per-tool MCP exposure. Mirrors useToggleRouteMutation but
 * targets the tool_config table (MCP visibility) rather than route embeddings.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { enableTool, disableTool, setToolEmbeddingModel, hideTool, showTool, setToolPermission, setToolState, setToolsBatch } from "@/api/tools"
import { hidePluginTools, showPluginTools } from "@/api/plugins"
import type { PluginToolsResponse } from "@/api/plugins"
import type { ToolPermission } from "@/api/tools"

/** Hook to set unified state for a specific tool. */
export function useSetToolStateMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginTools", pluginId] as const

  return useMutation<
    { plugin_id: string; tool_name: string; state: "enabled" | "hidden" | "disabled"; is_enabled: boolean; is_hidden: boolean },
    Error,
    { toolName: string; state: "enabled" | "hidden" | "disabled" },
    { previous: PluginToolsResponse | undefined }
  >({
    mutationFn: ({ toolName, state }) =>
      setToolState(pluginId, toolName, state),

    onMutate: async ({ toolName, state }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<PluginToolsResponse>(KEY)
      qc.setQueryData<PluginToolsResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          tools: old.tools.map((t) =>
            t.name === toolName
              ? {
                  ...t,
                  is_enabled: state !== "disabled",
                  is_hidden: state === "hidden",
                }
              : t
          ),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
}

/**
 * Mutation to expose or hide a single tool for a plugin.
 * Optimistically flips the matching tool's is_enabled flag; reverts on error.
 */
export function useToggleToolMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginTools", pluginId] as const

  return useMutation<
    { plugin_id: string; tool_name: string; enabled: boolean },
    Error,
    { toolName: string; enable: boolean },
    { previous: PluginToolsResponse | undefined }
  >({
    mutationFn: ({ toolName, enable }) =>
      enable ? enableTool(pluginId, toolName) : disableTool(pluginId, toolName),

    onMutate: async ({ toolName, enable }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<PluginToolsResponse>(KEY)
      qc.setQueryData<PluginToolsResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          tools: old.tools.map((t) =>
            t.name === toolName ? { ...t, is_enabled: enable } : t
          ),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
}

/** Hook to set or clear the embedding model assigned to a specific tool. */
export function useSetToolEmbeddingModelMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginTools", pluginId] as const

  return useMutation<
    { plugin_id: string; tool_name: string; embedding_model: string | null },
    Error,
    { toolName: string; modelId: string | null },
    { previous: PluginToolsResponse | undefined }
  >({
    mutationFn: ({ toolName, modelId }) =>
      setToolEmbeddingModel(pluginId, toolName, modelId),

    onMutate: async ({ toolName, modelId }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<PluginToolsResponse>(KEY)
      qc.setQueryData<PluginToolsResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          tools: old.tools.map((t) =>
            t.name === toolName ? { ...t, embedding_model: modelId } : t
          ),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
}

/**
 * Mutation to gateway-hide or show a single tool.
 * Optimistically updates is_hidden; reverts on error.
 */
export function useToggleToolHideMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginTools", pluginId] as const

  return useMutation<
    { plugin_id: string; tool_name: string; hidden: boolean },
    Error,
    { toolName: string; hide: boolean },
    { previous: PluginToolsResponse | undefined }
  >({
    mutationFn: ({ toolName, hide }) =>
      hide ? hideTool(pluginId, toolName) : showTool(pluginId, toolName),

    onMutate: async ({ toolName, hide }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<PluginToolsResponse>(KEY)
      qc.setQueryData<PluginToolsResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          tools: old.tools.map((t) =>
            t.name === toolName ? { ...t, is_hidden: hide } : t
          ),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
}

/**
 * Mutation for bulk plugin hide-all (POST) / show-all (DELETE) of gateway hides.
 * The caller typically invalidates or refetches the pluginTools key.
 */
export function useTogglePluginHideAllMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginTools", pluginId] as const

  return useMutation<
    { plugin_id: string; tools_hidden: boolean; applied: number },
    Error,
    { hide: boolean },
    { previous: PluginToolsResponse | undefined }
  >({
    mutationFn: ({ hide }) =>
      hide ? hidePluginTools(pluginId) : showPluginTools(pluginId),

    onMutate: async ({ hide }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<PluginToolsResponse>(KEY)
      qc.setQueryData<PluginToolsResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          tools: old.tools.map((t) => ({ ...t, is_hidden: hide })),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
}


/**
 * Mutation to set per-tool permission policy.
 * Optimistically applies; server returns the stored policy.
 */
export function useSetToolPermissionMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginTools", pluginId] as const

  return useMutation<
    { plugin_id: string; tool_name: string; permission: ToolPermission | null },
    Error,
    { toolName: string; permission: Partial<ToolPermission> },
    { previous: PluginToolsResponse | undefined }
  >({
    mutationFn: ({ toolName, permission }) => setToolPermission(pluginId, toolName, permission),

    onMutate: async ({ toolName, permission }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<PluginToolsResponse>(KEY)
      qc.setQueryData<PluginToolsResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          tools: old.tools.map((t) =>
            t.name === toolName ? { ...t, permission: { ...(t.permission || {}), ...permission } } : t
          ),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
}

/** Hook to set state and/or permission for a batch of tools. */
export function useBatchToolStateMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginTools", pluginId] as const

  return useMutation<
    { plugin_id: string; updated: Array<{ tool_name: string; state?: "enabled" | "hidden" | "disabled"; is_enabled?: boolean; is_hidden?: boolean; permission?: ToolPermission }>; count: number },
    Error,
    {
      toolNames: string[]
      state?: "enabled" | "hidden" | "disabled"
      permission?: Partial<ToolPermission>
    },
    { previous: PluginToolsResponse | undefined }
  >({
    mutationFn: ({ toolNames, state, permission }) =>
      setToolsBatch(pluginId, toolNames, { state, permission }),

    onMutate: async ({ toolNames, state, permission }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<PluginToolsResponse>(KEY)
      qc.setQueryData<PluginToolsResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          tools: old.tools.map((t) => {
            if (!toolNames.includes(t.name)) return t

            const updatedTool = { ...t }
            if (state !== undefined) {
              updatedTool.is_enabled = state !== "disabled"
              updatedTool.is_hidden = state === "hidden"
            }
            if (permission !== undefined) {
              updatedTool.permission = {
                ...(updatedTool.permission || {}),
                ...permission,
              }
            }
            return updatedTool
          }),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
    },
  })
}
