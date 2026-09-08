/**
 * Layer2Stub Component
 *
 * Manages External OAuth (Layer 2) connections for specific plugins.
 * It provides a UI for connecting or revoking external provider authorizations,
 * displaying the current connection status for each provider associated with a plugin.
 */

import type { FC } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { revokeOAuth } from "@/api/relay"

export interface Layer2StubProps {
  className?: string
  /** When provided, renders live OAuth status rows instead of the stub. */
  pluginId?: string
  providers?: string[]
  oauthStatus?: Record<string, string>
}

/**
 * Renders external OAuth provider statuses and connection/revocation controls.
 */
const Layer2Stub: FC<Layer2StubProps> = ({ className, pluginId, providers, oauthStatus }) => {
  const qc = useQueryClient()

  const { mutate: revoke, isPending: revoking } = useMutation({
    mutationFn: ({ provider }: { provider: string }) =>
      revokeOAuth(pluginId!, provider),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["plugins"] }),
  })

  // Stub mode — no plugin context
  if (!pluginId || !providers || providers.length === 0) {
    return (
      <div className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}>
        <Badge variant="outline" className="font-mono text-xs">
          Layer 2
        </Badge>
        <span>External OAuth relay</span>
      </div>
    )
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {providers.map((provider) => {
        const status = oauthStatus?.[provider] ?? "not_connected"
        const connected = status === "connected"
        return (
          <div key={provider} className="flex items-center gap-2 text-sm">
            <Badge variant="outline" className="font-mono text-xs capitalize">
              {provider}
            </Badge>
            <Badge variant={connected ? "default" : "secondary"} className="text-xs">
              {connected ? "connected" : "not connected"}
            </Badge>
            {connected ? (
              <Button
                size="sm"
                variant="destructive"
                className="ml-auto h-6 px-2 text-xs"
                disabled={revoking}
                onClick={(e) => {
                  e.stopPropagation()
                  revoke({ provider })
                }}
                onKeyDown={(e) => e.stopPropagation()}
              >
                Revoke
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="ml-auto h-6 px-2 text-xs"
                asChild
              >
                <a
                  href={`/connect?plugin_id=${pluginId}&provider=${provider}`}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  Connect
                </a>
              </Button>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Layer2Stub Component.
 * Renders the UI and handles state for the Layer2Stub feature.
 */
export default Layer2Stub
