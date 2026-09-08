/**
 * TanStack Query hooks for the live operation catalog and generated catalog client.
 */

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { getCatalog, type CatalogResponse } from "@/api/catalog"
import { setCatalogSnapshot } from "@/api/catalogRuntime"
import {
  createCatalogClient,
  type CatalogClient,
} from "@/api/generated/createCatalogClient"
import { useAuthedQueryEnabled } from "./useAuthedQuery"

/** Stable empty list so loading catalog does not recreate CatalogClient each render. */
const EMPTY_OPERATIONS: CatalogResponse["operations"] = []

/**
 * Load the entitlement-filtered operation catalog for the console.
 * Also seeds the module-level catalog runtime used by `callCatalogOp`.
 */
export function useCatalogQuery(opts?: { pluginId?: string; slot?: string }) {
  const enabled = useAuthedQueryEnabled()
  return useQuery({
    queryKey: ["catalog", opts?.pluginId ?? null, opts?.slot ?? null],
    queryFn: async (): Promise<CatalogResponse> => {
      const data = await getCatalog({
        pluginId: opts?.pluginId,
        slot: opts?.slot,
      })
      // 304 → null should not happen on first load without etag; treat as empty
      const snap = data ?? { revision: 0, etag: "", operations: [] }
      // Single sync point for module-level API clients (no useEffect mirror).
      if (!opts?.pluginId && !opts?.slot && data) {
        setCatalogSnapshot(data)
      }
      return snap
    },
    staleTime: 15_000,
    enabled,
  })
}

/**
 * Catalog query + generateClient-style CatalogClient bound to the latest ops snapshot.
 */
export function useCatalogClient(opts?: { pluginId?: string; slot?: string }): {
  client: CatalogClient
  revision: number
  etag: string
  isLoading: boolean
  error: Error | null
  operations: CatalogResponse["operations"]
} {
  const query = useCatalogQuery(opts)
  const operations = query.data?.operations ?? EMPTY_OPERATIONS
  const client = useMemo(
    () => createCatalogClient(operations),
    [operations],
  )

  return {
    client,
    revision: query.data?.revision ?? 0,
    etag: query.data?.etag ?? "",
    isLoading: query.isLoading,
    error: (query.error as Error | null) ?? null,
    operations,
  }
}
