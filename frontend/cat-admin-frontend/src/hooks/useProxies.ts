import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  getProxies,
  addProxy,
  removeProxy,
  testProxy,
  startProxyOAuth,
  reindexProxy,
  updateProxyDescription,
  type AddProxyPayload,
  type StartProxyOAuthResponse,
} from "@/api/proxies"
import type { ProxyServer } from "@/types/proxy"
import { bus } from "@/events/bus"
import { useAuthedQueryEnabled } from "./useAuthedQuery"

const PROXIES_KEY = ["proxies"] as const

/**
 * Hook to retrieve all registered upstream proxies.
 */
export function useProxiesQuery() {
  const enabled = useAuthedQueryEnabled()
  return useQuery({
    queryKey: PROXIES_KEY,
    queryFn: getProxies,
    enabled,
  })
}

/**
 * Mutation hook to register a new proxy.
 */
export function useAddProxyMutation() {
  const qc = useQueryClient()

  return useMutation<ProxyServer, Error, AddProxyPayload, { previous: ProxyServer[] | undefined }>({
    mutationFn: addProxy,
    onMutate: async (newProxy) => {
      await qc.cancelQueries({ queryKey: PROXIES_KEY })
      const previous = qc.getQueryData<ProxyServer[]>(PROXIES_KEY)
      
      qc.setQueryData<ProxyServer[]>(PROXIES_KEY, (old) => {
        if (!old) return []
        const optimisticRow: ProxyServer = {
          id: `optimistic-${Date.now()}`,
          name: newProxy.name,
          transport: newProxy.transport,
          url: newProxy.url,
          status: "inactive",
          hasAuth: newProxy.authMode !== "none",
          authMode: newProxy.authMode,
          oauthStatus: newProxy.authMode === "oauth" ? "not_connected" : "connected",
          toolCount: 0,
        }
        return [...old, optimisticRow]
      })

      return { previous }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(PROXIES_KEY, ctx.previous)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: PROXIES_KEY })
      bus.emit("proxy:changed")
    },
  })
}

/**
 * Mutation hook to delete a proxy.
 */
export function useRemoveProxyMutation() {
  const qc = useQueryClient()

  return useMutation<{ ok: boolean; restartRequired: boolean }, Error, string, { previous: ProxyServer[] | undefined }>({
    mutationFn: removeProxy,
    onMutate: async (name) => {
      await qc.cancelQueries({ queryKey: PROXIES_KEY })
      const previous = qc.getQueryData<ProxyServer[]>(PROXIES_KEY)
      
      qc.setQueryData<ProxyServer[]>(PROXIES_KEY, (old) => {
        if (!old) return []
        return old.filter((p) => p.name !== name)
      })

      return { previous }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(PROXIES_KEY, ctx.previous)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: PROXIES_KEY })
      bus.emit("proxy:changed")
    },
  })
}

/**
 * Mutation hook to test/reconnect a proxy.
 */
export function useTestProxyMutation() {
  const qc = useQueryClient()

  return useMutation<ProxyServer, Error, string, { previous: ProxyServer[] | undefined }>({
    mutationFn: testProxy,
    onMutate: async (name) => {
      await qc.cancelQueries({ queryKey: PROXIES_KEY })
      const previous = qc.getQueryData<ProxyServer[]>(PROXIES_KEY)
      
      qc.setQueryData<ProxyServer[]>(PROXIES_KEY, (old) => {
        if (!old) return []
        return old.map((p) => (p.name === name ? { ...p, status: "inactive" } : p))
      })

      return { previous }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(PROXIES_KEY, ctx.previous)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: PROXIES_KEY })
      bus.emit("proxy:changed")
    },
  })
}

/**
 * Mutation hook to initiate PKCE OAuth for a proxy.
 */
export function useStartProxyOAuthMutation() {
  return useMutation<StartProxyOAuthResponse, Error, string>({
    mutationFn: startProxyOAuth,
  })
}

/**
 * Mutation hook to reindex a proxy's route embeddings.
 */
export function useReindexProxyMutation() {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (name: string) => reindexProxy(name),
    onSuccess: (_data, name) => {
      void qc.invalidateQueries({ queryKey: PROXIES_KEY })
      void qc.invalidateQueries({ queryKey: ["routesAggregate"] })
      bus.emit("proxy:reindexed", { name })
    },
  })
}

/**
 * Mutation hook to update the custom description or workspace label of a proxy.
 */
export function useUpdateProxyDescriptionMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, customDescription, workspaceLabel }: { name: string; customDescription?: string | null; workspaceLabel?: string | null }) =>
      updateProxyDescription(name, customDescription, workspaceLabel),
    onSuccess: (_data, { name }) => {
      void qc.invalidateQueries({ queryKey: PROXIES_KEY })
      void qc.invalidateQueries({ queryKey: ["routesAggregate"] })
      bus.emit("proxy:reindexed", { name })
    },
  })
}

