/**
 * Route Embeddings API
 *
 * Read/toggle route-embedding enabled state per plugin.
 * Paths via OperationCatalog (owner `api.route`).
 */

import { catalogClient } from "./catalogClient"

export interface RouteAggregate {
  plugin_id: string
  route_count: number
  enabled_count: number
  all_enabled: boolean
  first_embedded_at: string | null
}

export interface RouteDetail {
  id: number
  operation_id: string
  method: string
  path_template: string
  description: string
  is_enabled: boolean
  is_fast_path: boolean
  embedded_at: string | null
}

export interface RoutesAggregateResponse {
  routes: RouteAggregate[]
}

export interface RoutesDetailResponse {
  plugin_id: string
  routes: RouteDetail[]
}

export interface RouteToggleResponse {
  plugin_id: string
  enabled: boolean
  updated_count: number
}

/**
 * Returns one aggregate row per plugin with route counts and enabled status.
 */
export function getRoutesAggregate(): Promise<RoutesAggregateResponse> {
  return catalogClient.routes.aggregate()
}

/**
 * Returns individual route rows for a specific plugin.
 */
export function getPluginRoutes(pluginId: string): Promise<RoutesDetailResponse> {
  return catalogClient.routes.forPlugin(pluginId)
}

/**
 * Enables all route embeddings for a plugin (is_enabled = TRUE).
 */
export async function enablePluginRoutes(pluginId: string): Promise<RouteToggleResponse> {
  return catalogClient.routes.enablePluginRoutes(pluginId)
}

/**
 * Disables all route embeddings for a plugin (is_enabled = FALSE).
 */
export async function disablePluginRoutes(pluginId: string): Promise<RouteToggleResponse> {
  return catalogClient.routes.disablePluginRoutes(pluginId)
}

/**
 * Enables an individual route embedding for a plugin (is_enabled = TRUE).
 */
export async function enableRoute(
  pluginId: string,
  routeId: number,
): Promise<{ id: number; enabled: boolean; plugin_id: string }> {
  return catalogClient.routes.enableRoute(pluginId, routeId)
}

/**
 * Disables an individual route embedding for a plugin (is_enabled = FALSE).
 */
export async function disableRoute(
  pluginId: string,
  routeId: number,
): Promise<{ id: number; enabled: boolean; plugin_id: string }> {
  return catalogClient.routes.disableRoute(pluginId, routeId)
}
