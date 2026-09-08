import { createFileRoute } from "@tanstack/react-router"
import { useSessionStore } from "@/store"
import { ConsolePage } from "@/components/console/ConsolePage"

/**
 * Index route configuration.
 *
 * Layer-1 MCP return lands on `/?state=…`. Capture into the persisted session
 * store in `beforeLoad` (not a useEffect URL↔Zustand sync loop); `ConsolePage`
 * only clears the query string and reads `useMcpState()` for the banner.
 */
export const Route = createFileRoute("/")({
  validateSearch: (s: Record<string, unknown>) => ({
    oauth_success: typeof s.oauth_success === "string" ? s.oauth_success : undefined,
    state: typeof s.state === "string" && s.state.length <= 500 ? s.state : undefined,
  }),
  beforeLoad: ({ search }) => {
    // Capture-then-clear: OAuth redirect origin cannot write Zustand directly.
    if (search.state) {
      useSessionStore.getState().setMcpState(search.state)
    }
  },
  component: ConsolePage,
})
