/**
 * Module-level catalog bootstrap for non-hook API modules.
 *
 * Session-gated host routes are published as OperationDescriptors
 * (`plugin_id` = route owner, e.g. `api.plugins`). Hand-written FE modules
 * call through `callCatalogOp` instead of hardcoding paths.
 */

import { getCatalog, type CatalogOperation, type CatalogResponse } from "./catalog"
import { createCatalogClient, type CatalogClient } from "./generated/createCatalogClient"

let _snapshot: CatalogResponse | null = null
let _client: CatalogClient | null = null
let _loadPromise: Promise<CatalogClient> | null = null

/**
 * Replace the in-memory catalog snapshot (used by hooks after fetch).
 */
export function setCatalogSnapshot(data: CatalogResponse | null): void {
  _snapshot = data
  _client = data ? createCatalogClient(data.operations) : null
}

/**
 * Current operations snapshot (may be empty before first load).
 */
export function getCatalogOperations(): CatalogOperation[] {
  return _snapshot?.operations ?? []
}

/**
 * Synchronous client if already loaded; otherwise null.
 */
export function peekCatalogClient(): CatalogClient | null {
  return _client
}

/**
 * Ensure the live catalog is loaded (deduped). Safe to call from API modules.
 *
 * A `null` result from `getCatalog()` means "304, reuse the prior snapshot" —
 * it must never be coerced into an empty client, since a browser-level
 * revalidation the app never asked for (see `getCatalog` doc) would then
 * permanently blank every subsequent `callCatalogOp` lookup. With no prior
 * client to fall back on, retry once before giving up; a caller that still
 * gets nothing throws so the next call re-fetches instead of caching a dead
 * empty catalog forever.
 */
export async function ensureCatalogClient(): Promise<CatalogClient> {
  if (_client && _snapshot) return _client
  if (_loadPromise) return _loadPromise

  _loadPromise = (async () => {
    try {
      let data = await getCatalog()
      if (!data && !_client) {
        data = await getCatalog()
      }
      if (data) {
        setCatalogSnapshot(data)
      } else if (!_client) {
        throw new Error("Catalog snapshot unavailable (repeated 304 with no prior data)")
      }
      return _client!
    } finally {
      _loadPromise = null
    }
  })()

  return _loadPromise
}

/**
 * Invalidate and force the next ensureCatalogClient to re-fetch.
 */
export function invalidateCatalogClient(): void {
  _snapshot = null
  _client = null
  _loadPromise = null
}

/**
 * Call a catalog operation by (plugin_id, operation_id).
 * Loads the catalog on first use. Prefers HTTP exposure paths from the snapshot.
 *
 * Snapshot misses refresh-once inside `CatalogClient.call` (peek the live
 * module snapshot, then re-fetch) so playground / `useCatalogClient().op`
 * get the same retry as typed `catalogClient` namespaces. Never falls back
 * to a blind `/execute` POST — host console ops are 501 there.
 */
export async function callCatalogOp<T = unknown>(
  pluginId: string,
  operationId: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const client = await ensureCatalogClient()
  return (await client.call(pluginId, operationId, args)) as T
}

/**
 * Resolve the filled HTTP path for an op (for SSE / raw fetch that needs a URL).
 * Returns null if the op has no HTTP exposure.
 */
export async function resolveCatalogPath(
  pluginId: string,
  operationId: string,
  args: Record<string, unknown> = {},
): Promise<{ path: string; method: string } | null> {
  const client = await ensureCatalogClient()
  const op = client.get(pluginId, operationId)
  if (!op?.http?.method || !op.http.path_template) return null
  const { resolveHttpCall } = await import("./inferenceClient")
  const resolved = resolveHttpCall(op.http.path_template, op.http.method, args)
  return { path: resolved.path, method: resolved.method }
}
