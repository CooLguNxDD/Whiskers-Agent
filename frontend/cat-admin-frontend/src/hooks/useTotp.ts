import { useQuery } from "@tanstack/react-query"
import { getTotpStatus, type TotpStatusResponse } from "@/api/terminal"
import { useAuthedQueryEnabled } from "./useAuthedQuery"

/**
 * Query hook to fetch TOTP authentication status for terminal elevation.
 */
export function useTotpStatusQuery() {
  const enabled = useAuthedQueryEnabled()
  return useQuery<TotpStatusResponse>({
    queryKey: ["totp_status"],
    queryFn: getTotpStatus,
    enabled,
    staleTime: 60000,
    retry: false,
  })
}
