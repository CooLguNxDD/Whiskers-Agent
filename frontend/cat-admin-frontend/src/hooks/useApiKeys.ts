/**
 * API Keys Controller Hooks
 *
 * All TanStack Query hooks and scope-editor logic for the API Keys page.
 * Follows the MVC pattern: Services (api/) -> Controllers (hooks/) -> Views (components/).
 */

import { useQuery, useMutation, useQueryClient, useQueries } from "@tanstack/react-query"
import type { UseQueryResult } from "@tanstack/react-query"

import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  deleteApiKey,
  updateApiKeyScopes,
  listScopePresets,
  createScopePreset,
  deleteScopePreset,
  getScopeVocabulary,
  type ScopeVocabulary,
} from "@/api/apiKeys"
import type { ApiKey, CreateApiKeyResponse, ScopePreset } from "@/api/apiKeys"
import { getPlugins, getPluginTools } from "@/api/plugins"
import type { Plugin } from "@/types/plugin"

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

/** Query key for the API keys list — invalidated after create/revoke/delete/scope-update. */
export const API_KEYS_QUERY_KEY = ["apiKeys"] as const
/** Query key for the plugins list, used here to populate the scope editor's plugin picker. */
export const PLUGINS_QUERY_KEY = ["plugins"] as const
/** Query key factory for a plugin's tools list (scoped by plugin id). */
export const pluginToolsQueryKey = (id: string) => ["pluginTools", id] as const
/** Query key for the caller's saved scope presets, invalidated after create/delete. */
export const SCOPE_PRESETS_QUERY_KEY = ["scopePresets"] as const
/** Query key for the server's full scope vocabulary (core/plugin/group/op grammar), used to render the scope picker. */
export const SCOPE_VOCABULARY_QUERY_KEY = ["scopeVocabulary"] as const

// ---------------------------------------------------------------------------
// Server-state hooks
// ---------------------------------------------------------------------------

/** Lists all active API keys. */
export function useApiKeysQuery(): UseQueryResult<ApiKey[]> {
  return useQuery<ApiKey[]>({
    queryKey: API_KEYS_QUERY_KEY,
    queryFn: listApiKeys,
  })
}

/** Creates a new API key. Calls onToken with the response on success. */
export function useCreateApiKeyMutation(onToken: (resp: CreateApiKeyResponse) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ keyName, secs }: { keyName: string; secs?: number }) =>
      createApiKey(keyName, secs),
    onSuccess: (data) => {
      onToken(data)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY })
    },
  })
}

/** Rotates (revoke + replace) an API key. Calls onToken with the new key on success. */
export function useRevokeApiKeyMutation(onToken: (resp: CreateApiKeyResponse) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (keyId: string) => revokeApiKey(keyId),
    onSuccess: (data) => {
      onToken(data)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY })
    },
  })
}

/** Permanently deletes an API key. */
export function useDeleteApiKeyMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (keyId: string) => deleteApiKey(keyId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY })
    },
  })
}

/** Updates the scope list for an API key. Calls onSuccess when done. */
export function useUpdateApiKeyScopesMutation(onSuccess: () => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ keyId, scopes }: { keyId: string; scopes: string[] | null }) =>
      updateApiKeyScopes(keyId, scopes),
    onSuccess: () => {
      onSuccess()
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY })
    },
  })
}

/** Retrieves the list of scope presets. Only fires when enabled=true. */
export function useScopePresetsQuery(enabled: boolean): UseQueryResult<ScopePreset[]> {
  return useQuery<ScopePreset[]>({
    queryKey: SCOPE_PRESETS_QUERY_KEY,
    queryFn: listScopePresets,
    enabled,
  })
}

/** Saves a new scope preset. Calls onSuccess with the preset on success. */
export function useSaveScopePresetMutation(onSuccess?: (preset: ScopePreset) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, scopes }: { name: string; scopes: string[] | null }) =>
      createScopePreset(name, scopes),
    onSuccess: (data) => {
      onSuccess?.(data)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SCOPE_PRESETS_QUERY_KEY })
    },
  })
}

/** Permanently deletes a scope preset. */
export function useDeleteScopePresetMutation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (presetId: string) => deleteScopePreset(presetId),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SCOPE_PRESETS_QUERY_KEY })
    },
  })
}

/** Fetches the installed plugin list. Only fires when enabled=true (scope config mode). */
export function usePluginsQuery(enabled: boolean) {
  return useQuery({
    queryKey: PLUGINS_QUERY_KEY,
    queryFn: getPlugins,
    enabled,
  })
}

/**
 * Fetches the configuration-driven valid global scope vocabulary.
 * Only fires when enabled=true.
 */
export function useScopeVocabularyQuery(enabled: boolean): UseQueryResult<ScopeVocabulary> {
  return useQuery<ScopeVocabulary>({
    queryKey: SCOPE_VOCABULARY_QUERY_KEY,
    queryFn: getScopeVocabulary,
    enabled,
  })
}

/** Parallel tool fetches for all plugins -- used to derive route-group scopes. */
export function usePluginToolsQueries(plugins: Plugin[], enabled: boolean) {
  return useQueries({
    queries: plugins.map((p) => ({
      queryKey: pluginToolsQueryKey(p.id),
      queryFn: () => getPluginTools(p.id),
      enabled: enabled && plugins.length > 0,
    })),
  })
}

// Scope-editor composable (local state only) lives in ./useScopeEditor.ts
