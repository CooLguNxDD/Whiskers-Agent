/**
 * useDirectCredentials Hooks
 *
 * Mutation hooks for saving and clearing direct username/password credentials.
 * Invalidates the plugins query cache on settle to refresh direct auth status pills/badges.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { setDirectCredentials, clearDirectCredentials, type DirectCredentialsBody } from "@/api/directCreds"

/**
 * Hook to set direct credentials.
 */
export function useSetDirectCredentialsMutation(pluginId: string) {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: (body: DirectCredentialsBody) => setDirectCredentials(pluginId, body),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["plugins"] })
      void qc.invalidateQueries({ queryKey: ["pluginHealth", pluginId] })
    },
  })
}

/**
 * Hook to clear direct credentials.
 */
export function useClearDirectCredentialsMutation(pluginId: string) {
  const qc = useQueryClient()

  return useMutation({
    mutationFn: () => clearDirectCredentials(pluginId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["plugins"] })
      void qc.invalidateQueries({ queryKey: ["pluginHealth", pluginId] })
    },
  })
}
