/**
 * useRoutes Hooks
 *
 * Hooks for reading and toggling route-embedding enabled state per plugin.
 * Follows the same optimistic-update pattern as useTogglePluginMutation.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  getPluginRoutes,
  enablePluginRoutes,
  disablePluginRoutes,
  enableRoute,
  disableRoute,
  type RoutesDetailResponse,
} from "@/api/routes"

/**
 * Fetches individual route rows for a plugin — used in the Routes tab of PluginDetail.
 */
export function usePluginRoutesQuery(pluginId: string) {
  return useQuery({
    queryKey: ["pluginRoutes", pluginId] as const,
    queryFn: () => getPluginRoutes(pluginId),
    enabled: !!pluginId,
  })
}

/**
 * Mutation to enable or disable ALL routes for a plugin at once.
 * Optimistically flips every route's is_enabled flag; reverts on error.
 */
export function useTogglePluginRoutesMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginRoutes", pluginId] as const

  return useMutation({
    mutationFn: (enable: boolean) =>
      enable ? enablePluginRoutes(pluginId) : disablePluginRoutes(pluginId),

    onMutate: async (enable: boolean) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<RoutesDetailResponse>(KEY)
      qc.setQueryData<RoutesDetailResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          routes: old.routes.map((r) => ({ ...r, is_enabled: enable })),
        }
      })
      return { previous }
    },

    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(KEY, ctx.previous)
    },

    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY })
      void qc.invalidateQueries({ queryKey: ["routesAggregate"] })
    },
  })
}

/**
 * Mutation to enable or disable a single route for a plugin.
 * Optimistically flips the matching route's is_enabled flag; reverts on error.
 */
export function useToggleRouteMutation(pluginId: string) {
  const qc = useQueryClient()
  const KEY = ["pluginRoutes", pluginId] as const

  return useMutation<
    { id: number; enabled: boolean; plugin_id: string },
    Error,
    { routeId: number; enable: boolean },
    { previous: RoutesDetailResponse | undefined }
  >({
    mutationFn: ({ routeId, enable }: { routeId: number; enable: boolean }) =>
      enable ? enableRoute(pluginId, routeId) : disableRoute(pluginId, routeId),

    onMutate: async ({ routeId, enable }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const previous = qc.getQueryData<RoutesDetailResponse>(KEY)
      qc.setQueryData<RoutesDetailResponse>(KEY, (old) => {
        if (!old) return old
        return {
          ...old,
          routes: old.routes.map((r) =>
            r.id === routeId ? { ...r, is_enabled: enable } : r
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
      void qc.invalidateQueries({ queryKey: ["routesAggregate"] })
    },
  })
}


