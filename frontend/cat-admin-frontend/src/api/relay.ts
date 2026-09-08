/**
 * Relay API
 *
 * Layer 2 OAuth relay status + revoke via OperationCatalog.
 */

import { catalogClient } from "./catalogClient"

/**
 * Retrieves the current relay status from the Layer 2 relay API.
 */
export function getRelayStatus(): Promise<{ configured: boolean }> {
  return catalogClient.relay.status()
}

/**
 * Revokes a specific External OAuth provider authorization for a plugin.
 */
export function revokeOAuth(pluginId: string, provider: string): Promise<void> {
  return catalogClient.plugins.revokeOAuth(pluginId, provider)
}
