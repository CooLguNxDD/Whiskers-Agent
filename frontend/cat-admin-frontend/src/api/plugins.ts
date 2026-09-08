/**
 * Plugins API
 *
 * Plugin list, enable/disable, health, tools, skills, logs, config.
 * Paths resolved via live OperationCatalog (owner `api.plugins`).
 */

import { catalogClient } from "./catalogClient"
import type { Plugin, PluginHealth } from "@/types/plugin"

export interface PluginsResponse {
  plugins: Plugin[]
  system_tier: number
  system_tier_name: string
}

/**
 * Retrieves the list of plugins and system configuration.
 */
export function getPlugins(): Promise<PluginsResponse> {
  return catalogClient.plugins.list()
}

/**
 * Enables a specific plugin by its ID.
 */
export function enablePlugin(id: string): Promise<Plugin> {
  return catalogClient.plugins.enable(id)
}

/**
 * Disables a specific plugin by its ID.
 */
export function disablePlugin(id: string): Promise<Plugin> {
  return catalogClient.plugins.disable(id)
}

/**
 * Hard-deletes a stale or inactive plugin row from the registry.
 */
export function deletePlugin(id: string): Promise<{ id: string; deleted: boolean }> {
  return catalogClient.plugins.delete(id)
}

/**
 * Retrieves operational health data for a specific plugin.
 */
export function getPluginHealth(id: string): Promise<PluginHealth> {
  return catalogClient.plugins.health(id)
}

export interface PluginTool {
  name: string
  description: string
  is_enabled: boolean
  is_hidden: boolean
  embedding_model?: string | null
  permission?: {
    allow_read?: boolean
    allow_write?: boolean
    require_confirmation?: boolean
  } | null
  group?: string
  access?: "read" | "write"
}

export interface PluginToolsResponse {
  plugin_id: string
  tools: PluginTool[]
}

/**
 * Retrieves the real tools list for a specific plugin.
 */
export function getPluginTools(pluginId: string): Promise<PluginToolsResponse> {
  return catalogClient.plugins.tools(pluginId)
}

/**
 * Triggers route embedding reindexing for a specific plugin by its ID.
 */
export function reindexPlugin(id: string): Promise<{ status: string; message: string }> {
  return catalogClient.plugins.reindex(id)
}

/**
 * Hides all tools associated with a specific plugin from the gateway.
 */
export function hidePluginTools(
  id: string,
): Promise<{ plugin_id: string; tools_hidden: boolean; applied: number }> {
  return catalogClient.plugins.hideTools(id)
}

/**
 * Shows all tools associated with a specific plugin on the gateway.
 */
export function showPluginTools(
  id: string,
): Promise<{ plugin_id: string; tools_hidden: boolean; applied: number }> {
  return catalogClient.plugins.showTools(id)
}

export interface PluginSkill {
  key: string
  content: string
  declared?: boolean
  /** sha256 of DB content (empty content → null) */
  content_hash?: string | null
  /** sha256 of on-disk skill body after frontmatter strip */
  fs_content_hash?: string | null
  on_disk?: boolean
  /** true when DB and FS hashes match */
  in_sync?: boolean
}

export interface PluginSkillsResponse {
  plugin_id: string
  skills: PluginSkill[]
  declared_from_manifest: string[]
}

export interface PluginSkillReloadResult {
  key: string
  content_hash?: string | null
  fs_content_hash?: string | null
  in_sync?: boolean
  declared?: boolean
  chars?: number
}

export interface PluginSkillReloadFailed {
  key: string
  error: string
  on_disk?: boolean
}

export interface PluginSkillsReloadResponse {
  plugin_id: string
  reloaded: PluginSkillReloadResult[]
  failed: PluginSkillReloadFailed[]
  message?: string
}

/**
 * Retrieves the persisted skill files (workflow docs) for a plugin.
 */
export function getPluginSkills(pluginId: string): Promise<PluginSkillsResponse> {
  return catalogClient.plugins.skills(pluginId)
}

/**
 * Create or update a single skill file (key + markdown content).
 */
export function savePluginSkill(
  pluginId: string,
  key: string,
  content: string,
): Promise<{
  plugin_id: string
  key: string
  saved: boolean
  content_hash?: string | null
  fs_content_hash?: string | null
  in_sync?: boolean
}> {
  return catalogClient.plugins.saveSkill(pluginId, key, content)
}

/**
 * Delete a skill file by its key.
 */
export function deletePluginSkill(
  pluginId: string,
  key: string,
): Promise<{ plugin_id: string; key: string; deleted: boolean }> {
  return catalogClient.plugins.deleteSkill(pluginId, key)
}

/**
 * Load skill file(s) from the plugin package on disk into DB + live registry.
 * Pass a key to reload one skill; omit to reload all listed keys that exist on disk.
 */
export function reloadPluginSkills(
  pluginId: string,
  key?: string,
): Promise<PluginSkillsReloadResponse> {
  return catalogClient.plugins.reloadSkills(pluginId, key)
}

export interface PluginLogEntry {
  id: number
  tool_name: string
  model: string | null
  latency_ms: number
  ok: boolean
  error_type: string | null
  status_code: number | null
  subject: string | null
  created_at: string | null
}

export interface PluginLogsResponse {
  plugin_id: string
  items: PluginLogEntry[]
  total: number
  page: number
  per_page: number
}

export type PluginLogStatusFilter = "all" | "ok" | "error"

/**
 * Retrieves a paginated page of tool-call log entries for a plugin.
 */
export function getPluginLogs(
  pluginId: string,
  page: number,
  perPage: number,
  status: PluginLogStatusFilter = "all",
): Promise<PluginLogsResponse> {
  return catalogClient.plugins.logs(pluginId, page, perPage, status)
}

export interface PluginConfigResponse {
  plugin_id: string
  config_filename: string
  base: Record<string, unknown> | null
  override: Record<string, unknown> | null
  effective: Record<string, unknown>
}

/**
 * Retrieves the on-disk base config.json, console override, and effective merge for a plugin.
 */
export function getPluginConfig(pluginId: string): Promise<PluginConfigResponse> {
  return catalogClient.plugins.config(pluginId)
}

/**
 * Saves a console-edited config override for a plugin. Applies on next plugin reload.
 */
export function savePluginConfig(
  pluginId: string,
  config: Record<string, unknown>,
): Promise<{ plugin_id: string; saved: boolean }> {
  return catalogClient.plugins.saveConfig(pluginId, config)
}

/**
 * Clears the config override, reverting to the on-disk base config.json.
 */
export function resetPluginConfig(
  pluginId: string,
): Promise<{ plugin_id: string; deleted: boolean }> {
  return catalogClient.plugins.resetConfig(pluginId)
}
