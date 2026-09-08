import type { FC } from "react"
import { Badge } from "@/components/ui/badge"
import type { Plugin } from "@/types/plugin"

export interface PluginAuthStatusProps {
  plugin: Plugin
}

/**
 * Displays the authentication status (direct or OAuth) of a plugin.
 */
export const PluginAuthStatus: FC<PluginAuthStatusProps> = ({ plugin }) => {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[10px] uppercase font-bold text-muted-foreground tracking-wider">
        Authentication Status
      </span>
      <div className="flex flex-wrap gap-2">
        {plugin.layer2_oauth_enabled ? (
          plugin.external_oauth_providers?.map((provider: string) => {
            const status = plugin.oauth_status?.[provider] ?? "not_connected"
            const connected = status === "connected"
            return (
              <div key={provider} className="flex items-center gap-1.5">
                <Badge variant="outline" className="font-mono text-[10px] capitalize">
                  {provider.replace(/_/g, " ")}
                </Badge>
                <Badge variant={connected ? "default" : "secondary"} className="text-[10px]">
                  {connected ? "connected" : "disconnected"}
                </Badge>
              </div>
            )
          })
        ) : (
          <div className="flex items-center gap-1.5">
            <Badge variant="outline" className="font-mono text-[10px]">
              Direct Login
            </Badge>
            <Badge variant={plugin.auth_status === "needs_reauth" ? "destructive" : "default"} className="text-[10px]">
              {plugin.auth_status === "needs_reauth" ? "needs auth" : "auth ok"}
            </Badge>
          </div>
        )}
      </div>
    </div>
  )
}
