/**
 * Proxies API
 *
 * Upstream proxy server endpoints via OperationCatalog (owner `api.proxy`).
 */

import { catalogClient } from "./catalogClient"
import type { ProxyServer, ProxyAuthMode } from "@/types/proxy"

/**
 * Payload for adding a new proxy server.
 */
export interface AddProxyPayload {
  name: string
  transport: "http" | "sse"
  url: string
  authMode: ProxyAuthMode
  bearerToken?: string
  oauthConfig?: {
    authorize_url?: string
    token_url?: string
    client_id?: string
    scopes?: string[]
    pkce?: string
    auth_header?: string
  }
  clientSecret?: string
  customDescription?: string
  workspaceLabel?: string
}

/**
 * Response for removing a proxy server.
 */
export interface RemoveProxyResponse {
  ok: boolean
  restartRequired: boolean
}

/**
 * Response for starting an OAuth flow on a proxy.
 */
export interface StartProxyOAuthResponse {
  authorizeUrl: string
}

/**
 * Retrieves the list of active upstream proxies.
 */
export function getProxies(): Promise<ProxyServer[]> {
  return catalogClient.proxies.list()
}

/**
 * Adds and registers a new upstream proxy.
 */
export function addProxy(payload: AddProxyPayload): Promise<ProxyServer> {
  return catalogClient.proxies.add(payload)
}

/**
 * Removes and unmounts an upstream proxy by its name.
 */
export function removeProxy(name: string): Promise<RemoveProxyResponse> {
  return catalogClient.proxies.remove(name)
}

/**
 * Tests and refreshes the connection to an upstream proxy.
 */
export function testProxy(name: string): Promise<ProxyServer> {
  return catalogClient.proxies.test(name)
}

/**
 * Initiates the Layer 2 outbound OAuth PKCE login flow.
 */
export function startProxyOAuth(name: string): Promise<StartProxyOAuthResponse> {
  return catalogClient.proxies.startOAuth(name)
}

/**
 * Triggers route embedding reindexing for a specific proxy by its name.
 */
export function reindexProxy(name: string): Promise<{ status: string; message: string }> {
  return catalogClient.proxies.reindex(name)
}

/**
 * Updates the custom description and workspace label for an upstream proxy.
 */
export function updateProxyDescription(
  name: string,
  customDescription?: string | null,
  workspaceLabel?: string | null,
): Promise<{ name: string; customDescription?: string | null; workspaceLabel?: string | null; status: string }> {
  return catalogClient.proxies.updateDescription(name, customDescription, workspaceLabel)
}
