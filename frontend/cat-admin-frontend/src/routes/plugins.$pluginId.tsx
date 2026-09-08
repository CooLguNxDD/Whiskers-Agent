import { createFileRoute } from "@tanstack/react-router"
import PluginDetail from "@/components/PluginDetail/PluginDetail"

/**
 * Plugin details route configuration.
 */
export const Route = createFileRoute("/plugins/$pluginId")({
  component: function PluginPage() {
    const { pluginId } = Route.useParams()
    return <PluginDetail pluginId={pluginId} />
  },
})
