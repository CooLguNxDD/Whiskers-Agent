/**
 * API Keys API
 *
 * List/create/revoke/delete keys and scope presets via OperationCatalog
 * (owner `api.apikeys`).
 */

import { catalogClient } from "./catalogClient"

export interface ApiKey {
  key_id: string
  name: string
  prefix: string
  status: "active" | "revoked"
  expires_at: string | null
  last_used_at: string | null
  created_at: string
  revoked_at: string | null
  scopes: string[] | null
}

export interface CreateApiKeyResponse {
  key_id: string
  token: string
  prefix: string
  name: string
  expires_at: string | null
}

export interface ScopePreset {
  id: string
  name: string
  scopes: string[] | null
  created_at: string
}

/**
 * Retrieves the list of API keys.
 */
export async function listApiKeys(): Promise<ApiKey[]> {
  const res = await catalogClient.apiKeys.list()
  return res.keys
}

/**
 * Creates a new API key.
 */
export function createApiKey(
  name: string,
  expiresIn?: number,
): Promise<CreateApiKeyResponse> {
  return catalogClient.apiKeys.create(name, expiresIn)
}

/**
 * Revokes an existing API key (returns a replacement key token).
 */
export function revokeApiKey(keyId: string): Promise<CreateApiKeyResponse> {
  return catalogClient.apiKeys.revoke(keyId)
}

/**
 * Permanently deletes an API key.
 */
export function deleteApiKey(keyId: string): Promise<{ ok: boolean }> {
  return catalogClient.apiKeys.delete(keyId)
}

/**
 * Updates the scopes of an API key.
 */
export function updateApiKeyScopes(
  keyId: string,
  scopes: string[] | null,
): Promise<{ ok: boolean }> {
  return catalogClient.apiKeys.updateScopes(keyId, scopes)
}

/**
 * Retrieves the list of scope presets.
 */
export async function listScopePresets(): Promise<ScopePreset[]> {
  const res = await catalogClient.apiKeys.listScopePresets()
  return res.presets
}

/**
 * Creates a new scope preset.
 */
export function createScopePreset(
  name: string,
  scopes: string[] | null,
): Promise<ScopePreset> {
  return catalogClient.apiKeys.createScopePreset(name, scopes)
}

/**
 * Permanently deletes a scope preset.
 */
export function deleteScopePreset(presetId: string): Promise<{ ok: boolean }> {
  return catalogClient.apiKeys.deleteScopePreset(presetId)
}

export interface PluginScopeContribution {
  token: string
  description: string
  plugin_id: string
  level?: number
  kind?: string
  access?: string
  id_or_domain?: string
}

export interface CoreScopeContribution {
  token: string
  description: string
  level?: number
  kind?: string
  domain?: string
  access?: string
  id_or_domain?: string
}

export interface ScopeVocabulary {
  global_scopes: string[]
  plugin_scopes?: PluginScopeContribution[]
  core_scopes?: CoreScopeContribution[]
  levels?: Record<string, string>
}

/**
 * Retrieves the configuration-driven list of valid global OAuth scopes.
 */
export function getScopeVocabulary(): Promise<ScopeVocabulary> {
  return catalogClient.apiKeys.getScopeVocabulary()
}
