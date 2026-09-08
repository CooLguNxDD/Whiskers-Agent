import { createFileRoute } from "@tanstack/react-router"
import AppShell from "@/components/shell/AppShell"
import { ProxyManager } from "@/components/ProxyManager/ProxyManager"

/**
 * Proxies route configuration.
 */
export const Route = createFileRoute("/proxies")({
  component: function ProxiesPage() {
    return (
      <AppShell active="proxies">
        <div className="ct-page-head">
          <div>
            <div className="ct-page-title">
              Upstream Proxies
              <span className="ct-eyebrow">/ Whiskers Agent</span>
            </div>
            <div className="ct-page-sub font-medium">
              Mount external HTTP/SSE MCP servers under local namespaces to bridge their tools into this node.
            </div>
          </div>
        </div>

        <div className="mt-4">
          <ProxyManager />
        </div>
      </AppShell>
    )
  },
})
