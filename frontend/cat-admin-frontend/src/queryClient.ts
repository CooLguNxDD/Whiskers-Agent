import { QueryClient } from "@tanstack/react-query"

/**
 * Global React Query client instance for managing server state.
 * Configured with a 5-minute stale time to optimize re-renders and network usage.
 * Sensitive to window focus events which will trigger background refetches.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 2,
      refetchOnWindowFocus: true,
    },
  },
})
