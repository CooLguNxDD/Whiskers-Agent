/**
 * useHealth Hook
 *
 * Polls GET /api/health for the admin shell Live status strip and node pill.
 */

import { useQuery } from "@tanstack/react-query"
import { getHealth, type SystemHealth } from "@/api/health"
import { useAuthedQueryEnabled } from "./useAuthedQuery"

/** Query key for the aggregated system health poll, shared with any consumer needing to invalidate/read the cached result. */
export const HEALTH_QUERY_KEY = ["health"] as const

/**
 * Fetch aggregated system health with a 15s poll interval.
 */
export function useHealthQuery() {
  const enabled = useAuthedQueryEnabled()
  return useQuery<SystemHealth>({
    queryKey: HEALTH_QUERY_KEY,
    queryFn: getHealth,
    staleTime: 10_000,
    refetchInterval: enabled ? 15_000 : false,
    retry: 1,
    enabled,
  })
}
