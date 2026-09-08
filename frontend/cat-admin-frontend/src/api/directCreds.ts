/**
 * Direct Credentials API
 *
 * Vault-backed username/password for plugins without Layer 2 OAuth.
 * Via OperationCatalog (owner `api.plugins`).
 */

import { catalogClient } from "./catalogClient"

export interface DirectCredentialsBody {
  username?: string
  password?: string
  api_token?: string
}

export interface DirectCredentialsResponse {
  status: "ok" | "auth_failed"
  auth_status: "ok" | "needs_reauth"
}

/**
 * Saves direct credentials to the Vault for a given plugin.
 */
export function setDirectCredentials(
  pluginId: string,
  body: DirectCredentialsBody,
): Promise<DirectCredentialsResponse> {
  return catalogClient.plugins.setDirectCredentials(pluginId, body)
}

/**
 * Clears direct credentials from the Vault for a given plugin.
 */
export function clearDirectCredentials(
  pluginId: string,
): Promise<{ status: string }> {
  return catalogClient.plugins.clearDirectCredentials(pluginId)
}
