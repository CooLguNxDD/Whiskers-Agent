/**
 * RevokeConfirmModal Component
 *
 * Prompt dialog asking the user to confirm revocation of an active plugin connection.
 */
import { type FC } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { AlertTriangle } from "lucide-react"
import { revokeOAuth } from "@/api/relay"
import { clearDirectCredentials } from "@/api/directCreds"
import type { Plugin, PluginHealth } from "@/types/plugin"

export interface RevokeConfirmModalProps {
  isOpen: boolean
  onOpenChange: (open: boolean) => void
  plugin: Plugin
  healthData?: PluginHealth | null
}

/**
 * RevokeConfirmModal Component
 *
 * Destructive confirmation modal to clear all active authentication integrations
 * and credentials associated with a plugin in a single unified action.
 */
export const RevokeConfirmModal: FC<RevokeConfirmModalProps> = ({
  isOpen,
  onOpenChange,
  plugin,
  healthData,
}) => {
  const qc = useQueryClient()
  const pluginId = plugin.id

  const connectedProviders =
    plugin.external_oauth_providers?.filter(
      (provider) => plugin.oauth_status?.[provider] === "connected"
    ) ?? []

  const hasDirectCreds = healthData?.credentials_present === true

  const { mutate: revokeEverything, isPending: revoking } = useMutation({
    mutationFn: async () => {
      const providerPromises = connectedProviders.map((provider) =>
        revokeOAuth(pluginId, provider)
      )
      const directPromise = hasDirectCreds
        ? clearDirectCredentials(pluginId)
        : Promise.resolve()

      await Promise.allSettled([...providerPromises, directPromise])
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["plugins"] })
      void qc.invalidateQueries({ queryKey: ["pluginHealth", pluginId] })
      onOpenChange(false)
    },
  })

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md bg-card border rounded-xl shadow-lg p-6">
        <DialogHeader className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="size-5 shrink-0" />
            <DialogTitle className="text-lg font-semibold tracking-tight">
              Revoke access for {plugin.name}?
            </DialogTitle>
          </div>
          <DialogDescription className="text-xs text-muted-foreground">
            This action will immediately wipe the following active integrations and credentials from the server. You will need to re-authenticate to use this plugin again.
          </DialogDescription>
        </DialogHeader>

        {/* List of active factors to clear */}
        <div className="my-4 space-y-2.5">
          <h4 className="text-[10px] uppercase font-bold text-muted-foreground tracking-wider">
            Connected factors to clear:
          </h4>
          <div className="flex flex-col gap-2 rounded-lg border bg-muted/20 p-3">
            {connectedProviders.length === 0 && !hasDirectCreds ? (
              <span className="text-xs text-muted-foreground italic">
                No active connections found.
              </span>
            ) : null}

            {connectedProviders.map((provider) => (
              <div key={provider} className="flex items-center justify-between gap-2 text-xs">
                <span className="font-semibold text-foreground capitalize">
                  {provider.replace(/_/g, " ")} OAuth Relay
                </span>
                <Badge variant="outline" className="text-[10px] uppercase font-semibold">
                  OAuth Token
                </Badge>
              </div>
            ))}

            {hasDirectCreds && (
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-semibold text-foreground">
                  Direct Credentials
                </span>
                <Badge variant="outline" className="text-[10px] uppercase font-semibold">
                  Username/Password
                </Badge>
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="flex flex-col sm:flex-row gap-2 sm:justify-end">
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs font-semibold"
            disabled={revoking}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="h-8 text-xs font-semibold px-4"
            disabled={revoking}
            onClick={() => revokeEverything()}
          >
            {revoking ? "Revoking..." : "Revoke everything"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * RevokeConfirmModal Component.
 * Renders the UI and handles state for the RevokeConfirmModal feature.
 */
export default RevokeConfirmModal
