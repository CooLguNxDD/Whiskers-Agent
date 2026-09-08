import { createFileRoute } from "@tanstack/react-router"
import { DEFAULT_PLUGIN_ID } from "@/constants/plugins"
import { ConnectPage } from "@/components/Connect/ConnectPage"

/**
 * Connection route configuration.
 */
export const Route = createFileRoute("/connect")({
  validateSearch: (s: Record<string, unknown>) => ({
    state: typeof s.state === "string" && s.state.length <= 500 ? s.state : "",
    oauth_success: typeof s.oauth_success === "string" ? s.oauth_success : "",
    plugin_id: typeof s.plugin_id === "string" ? s.plugin_id : DEFAULT_PLUGIN_ID,
    provider: typeof s.provider === "string" ? s.provider : "whiskers_core",
  }),
  component: ConnectPage,
})
