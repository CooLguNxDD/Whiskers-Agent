import { createFileRoute, Outlet } from "@tanstack/react-router"

/**
 * Plugins route configuration.
 */
export const Route = createFileRoute("/plugins")({
  component: function PluginsLayout() {
    return <Outlet />
  },
})
