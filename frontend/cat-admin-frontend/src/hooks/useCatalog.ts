/**
 * TanStack Query hooks for the live operation catalog and generated catalog client.
 */

import { useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
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
  const queryClient = useQueryClient()
  const pluginId = opts?.pluginId
  const slot = opts?.slot
  const queryKey = ["catalog", pluginId ?? null, slot ?? null] as const
  return useQuery({
    queryKey,
    queryFn: async (): Promise<CatalogResponse> => {
      const data = await getCatalog({ pluginId, slot })
      if (data) {
        // Only the unfiltered shell query owns the module-level snapshot.
        if (!pluginId && !slot) {
          setCatalogSnapshot(data)
        }
        return data
      }
      // 304: reuse *this query's* prior data. Never substitute the unfiltered
      // runtime snapshot into a pluginId/slot query, and never succeed with
      // an empty catalog on first load (see api/catalog.ts::getCatalog).
      const cached = queryClient.getQueryData<CatalogResponse>(queryKey)
      if (cached) return cached
      throw new Error("Catalog snapshot unavailable (304 with no prior data)")
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
