/**
 * usePlugins Hooks
 *
 * A collection of hooks for interacting with the plugins API.
 * Provides functionality for fetching plugins, toggling plugin states with optimistic updates,
 * and retrieving plugin health metrics.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  getPlugins,
  enablePlugin,
  disablePlugin,
  deletePlugin,
  getPluginHealth,
  getPluginTools,
  reindexPlugin,
  getPluginSkills,
  savePluginSkill,
  deletePluginSkill,
  reloadPluginSkills,
  getPluginLogs,
  getPluginConfig,
  savePluginConfig,
  resetPluginConfig,
  type PluginsResponse,
  type PluginLogStatusFilter,
} from "@/api/plugins"
import { disablePluginRoutes } from "@/api/routes"
import { revokeOAuth } from "@/api/relay"
import { bus } from "@/events/bus"
import { useAuthedQueryEnabled } from "./useAuthedQuery"

const PLUGINS_KEY = ["plugins"] as const

/**
 * Fetches the list of all available plugins and system-wide settings.
 */
export function usePluginsQuery() {
  const enabled = useAuthedQueryEnabled()
  return useQuery({
    queryKey: PLUGINS_KEY,
    queryFn: getPlugins,
    enabled,
  })
}

/**
 * Hook to retrieve a specific plugin's details from the cached plugins list.
 */
export function usePluginDetailQuery(id: string) {
  return useQuery({
    queryKey: [...PLUGINS_KEY, "detail", id],
    queryFn: async () => {
      const res = await getPlugins()
      const plugin = res.plugins.find((p) => p.id === id)
      if (!plugin) throw new Error("Plugin not found")
      return plugin
    },
  })
}

/**
 * Mutation hook for enabling or disabling a plugin.
 * Implements optimistic updates for immediate UI feedback.
 */
export function useTogglePluginMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      enabled ? enablePlugin(id) : disablePlugin(id),

    onMutate: async ({ id, enabled }) => {
      // Cancel outgoing refetches to avoid overwriting optimistic update
      await qc.cancelQueries({ queryKey: PLUGINS_KEY })
      // Snapshot the previous value for rollback on error
      const previous = qc.getQueryData<PluginsResponse>(PLUGINS_KEY)
      // Optimistically update the cache with the new status
      qc.setQueryData<PluginsResponse>(PLUGINS_KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          plugins: old.plugins.map((m) => (m.id === id ? { ...m, enabled } : m)),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData<PluginsResponse>(PLUGINS_KEY, ctx.previous)
      }
    },

    // Await the route-disable side effect here (not fire-and-forget in
    // onSuccess) so the mutation isn't considered settled — and route query
    // invalidation isn't fired — before it actually completes.
    onSettled: async (_data, error, { id, enabled }) => {
      void qc.invalidateQueries({ queryKey: PLUGINS_KEY })
      if (error || enabled) return
      try {
        await disablePluginRoutes(id)
      } catch (err) {
        console.error(`Failed to disable routes for plugin ${id}:`, err)
        // bus.emit("notification", { type: "error", message: "Failed to sync route rules." })
      } finally {
        void qc.invalidateQueries({ queryKey: ["pluginRoutes", id] })
        void qc.invalidateQueries({ queryKey: ["routesAggregate"] })
      }
    },

    onSuccess: (_data, { id, enabled }) => {
      bus.emit("plugin:toggled", { id, enabled })
    },
  })
}

/**
 * Mutation hook to revoke a plugin's Layer-2 external OAuth token for a provider.
 * Invalidates the plugins list and the plugin's health data on completion.
 */
export function useRevokeOAuthMutation(pluginId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (provider: string) => revokeOAuth(pluginId, provider),
    onMutate: async () => {
      // Avoid a concurrent refetch clobbering the optimistic/settled state.
      await qc.cancelQueries({ queryKey: PLUGINS_KEY })
      await qc.cancelQueries({ queryKey: ["pluginHealth", pluginId] })
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: PLUGINS_KEY })
      void qc.invalidateQueries({ queryKey: ["pluginHealth", pluginId] })
    },
  })
}

/**
 * Fetches health and operational metrics for a specific plugin by ID.
 */
export function usePluginHealthQuery(id: string) {
  return useQuery({
    queryKey: ["pluginHealth", id],
    queryFn: () => getPluginHealth(id),
  })
}

/**
 * Fetches real tools list for a specific plugin.
 */
export function usePluginToolsQuery(pluginId: string) {
  return useQuery({
    queryKey: ["pluginTools", pluginId] as const,
    queryFn: () => getPluginTools(pluginId),
    enabled: !!pluginId,
  })
}

/**
 * Helper hook to quickly retrieve the current system tier name.
 */
export function useSystemTier() {
  const { data } = usePluginsQuery()
  return data?.system_tier_name
}

/**
 * Mutation hook to reindex a plugin's route embeddings.
 */
export function useReindexPluginMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => reindexPlugin(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: PLUGINS_KEY })
      void qc.invalidateQueries({ queryKey: ["routesAggregate"] })
      bus.emit("plugin:reindexed", { id })
    },
  })
}

/**
 * Hard-delete a stale/inactive plugin row; invalidates the plugins list.
 */
export function useDeletePluginMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => deletePlugin(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: PLUGINS_KEY })
      bus.emit("plugin:deleted", { id })
    },
  })
}

const PLUGIN_SKILLS_KEY = (id: string) => ["pluginSkills", id] as const

/**
 * Fetches DB-persisted skill files for a plugin (for the Skills tab editor).
 */
export function usePluginSkillsQuery(pluginId: string) {
  return useQuery({
    queryKey: PLUGIN_SKILLS_KEY(pluginId),
    queryFn: () => getPluginSkills(pluginId),
    enabled: !!pluginId,
  })
}

/**
 * Save (create/update) a skill file. Invalidates skills query.
 */
export function useSavePluginSkillMutation(pluginId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, content }: { key: string; content: string }) => savePluginSkill(pluginId, key, content),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PLUGIN_SKILLS_KEY(pluginId) })
    },
  })
}

/**
 * Delete a skill file by key. Invalidates skills query.
 */
export function useDeletePluginSkillMutation(pluginId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (key: string) => deletePluginSkill(pluginId, key),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PLUGIN_SKILLS_KEY(pluginId) })
    },
  })
}

/**
 * Load skill(s) from on-disk package files into DB. Optional key for one skill.
 */
export function useReloadPluginSkillsMutation(pluginId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (key?: string) => reloadPluginSkills(pluginId, key),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PLUGIN_SKILLS_KEY(pluginId) })
    },
  })
}

/**
 * Fetches a paginated page of tool-call log entries for a plugin (for the Logs tab).
 */
export function usePluginLogsQuery(
  pluginId: string,
  page: number,
  perPage: number,
  status: PluginLogStatusFilter
) {
  return useQuery({
    queryKey: ["pluginLogs", pluginId, page, perPage, status] as const,
    queryFn: () => getPluginLogs(pluginId, page, perPage, status),
    enabled: !!pluginId,
    placeholderData: (prev) => prev,
  })
}

const PLUGIN_CONFIG_KEY = (id: string) => ["pluginConfig", id] as const

/**
 * Fetches the base config.json, console override, and effective merge for a plugin (Config tab).
 */
export function usePluginConfigQuery(pluginId: string) {
  return useQuery({
    queryKey: PLUGIN_CONFIG_KEY(pluginId),
    queryFn: () => getPluginConfig(pluginId),
    enabled: !!pluginId,
  })
}

/**
 * Save a console-edited config override for a plugin. Invalidates the config query.
 */
export function useSavePluginConfigMutation(pluginId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (config: Record<string, unknown>) => savePluginConfig(pluginId, config),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PLUGIN_CONFIG_KEY(pluginId) })
    },
  })
}

/**
 * Clear the config override, reverting to the on-disk base config.json.
 */
export function useResetPluginConfigMutation(pluginId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => resetPluginConfig(pluginId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PLUGIN_CONFIG_KEY(pluginId) })
    },
  })
}


