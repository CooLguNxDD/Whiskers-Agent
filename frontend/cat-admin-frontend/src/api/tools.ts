/**
 * Tool MCP-exposure API
 *
 * Toggle tool exposure, embedding model, hide, permissions, batch state.
 * Paths resolved via live OperationCatalog (owner `api.tool`).
 */

import { catalogClient } from "./catalogClient"

export interface ToolToggleResponse {
  plugin_id: string
  tool_name: string
  enabled: boolean
}

/**
 * Exposes a single tool to MCP clients (tool_config.is_enabled = TRUE).
 */
export function enableTool(
  pluginId: string,
  toolName: string,
): Promise<ToolToggleResponse> {
  return catalogClient.tools.enable(pluginId, toolName)
}

/**
 * Hides a single tool from MCP clients (tool_config.is_enabled = FALSE).
 */
export function disableTool(
  pluginId: string,
  toolName: string,
): Promise<ToolToggleResponse> {
  return catalogClient.tools.disable(pluginId, toolName)
}

export interface ToolEmbeddingModelResponse {
  plugin_id: string
  tool_name: string
  embedding_model: string | null
}

/**
 * Sets the embedding model for a single tool.
 */
export function setToolEmbeddingModel(
  pluginId: string,
  toolName: string,
  modelId: string | null,
): Promise<ToolEmbeddingModelResponse> {
  return catalogClient.tools.setEmbeddingModel(pluginId, toolName, modelId)
}

export interface ToolHideResponse {
  plugin_id: string
  tool_name: string
  hidden: boolean
  applied?: number
}

/**
 * Gateway-hide a single tool (still reachable via run_graph).
 */
export function hideTool(
  pluginId: string,
  toolName: string,
): Promise<ToolHideResponse> {
  return catalogClient.tools.hide(pluginId, toolName)
}

/**
 * Restore a gateway-hidden tool to direct MCP visibility.
 */
export function showTool(
  pluginId: string,
  toolName: string,
): Promise<ToolHideResponse> {
  return catalogClient.tools.show(pluginId, toolName)
}

export interface ToolPermission {
  allow_read?: boolean
  allow_write?: boolean
  require_confirmation?: boolean
}

export interface ToolPermissionResponse {
  plugin_id: string
  tool_name: string
  permission: ToolPermission | null
}

/**
 * Gets the current security permissions for a specific tool.
 */
export function getToolPermission(
  pluginId: string,
  toolName: string,
): Promise<ToolPermissionResponse> {
  return catalogClient.tools.getPermission(pluginId, toolName)
}

/**
 * Updates the security permissions for a specific tool.
 */
export function setToolPermission(
  pluginId: string,
  toolName: string,
  permission: Partial<ToolPermission>,
): Promise<ToolPermissionResponse> {
  return catalogClient.tools.setPermission(pluginId, toolName, permission)
}

export interface ToolStateResponse {
  plugin_id: string
  tool_name: string
  state: "enabled" | "hidden" | "disabled"
  is_enabled: boolean
  is_hidden: boolean
}

/**
 * Sets the unified state (enabled/hidden/disabled) for a tool.
 */
export function setToolState(
  pluginId: string,
  toolName: string,
  state: "enabled" | "hidden" | "disabled",
): Promise<ToolStateResponse> {
  return catalogClient.tools.setState(pluginId, toolName, state)
}

export interface BatchToolStateItem {
  tool_name: string
  state?: "enabled" | "hidden" | "disabled"
  is_enabled?: boolean
  is_hidden?: boolean
  permission?: ToolPermission
}

export interface BatchToolStateResponse {
  plugin_id: string
  updated: BatchToolStateItem[]
  count: number
}

/**
 * Sets state and/or permission for a batch of tools in a plugin.
 */
export function setToolsBatch(
  pluginId: string,
  toolNames: string[],
  patch: {
    state?: "enabled" | "hidden" | "disabled"
    permission?: Partial<ToolPermission>
  },
): Promise<BatchToolStateResponse> {
  return catalogClient.tools.setBatchState(pluginId, toolNames, patch)
}
