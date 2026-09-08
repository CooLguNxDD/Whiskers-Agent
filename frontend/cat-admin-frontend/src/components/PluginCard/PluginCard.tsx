/**
 * PluginCard Component
 *
 * A card display representing a single plugin summary in the plugin dashboard.
 */
import { useState, useMemo, type FC } from "react"
import { cn } from "@/lib/utils"
import { motion, AnimatePresence } from "framer-motion"
import { useShallow } from "zustand/react/shallow"
import { useUIStore } from "@/store"
import {
  useTogglePluginMutation,
  usePluginsQuery,
  usePluginHealthQuery,
  useDeletePluginMutation,
} from "@/hooks/usePlugins"
import { useMotionConfig } from "@/hooks/useMotion"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Link } from "@tanstack/react-router"
import { isPluginConnected } from "@/lib/pluginAuth"
import type { Plugin } from "@/types/plugin"
import { PluginAuthStatus } from "./components/PluginAuthStatus"
import { PluginCardHeader } from "./components/PluginCardHeader"
import RevokeConfirmModal from "@/components/ConnectModal/RevokeConfirmModal"

export interface PluginCardProps {
  /** Preferred: pass the plugin object from a parent list to avoid per-card find. */
  plugin?: Plugin
  /** Fallback id when `plugin` is not provided (loads list query and finds by id). */
  pluginId?: string
  className?: string
}

/**
 * Renders a card for a plugin with a toggle for enablement and an expandable detail section.
 */
const PluginCard: FC<PluginCardProps> = ({ plugin: pluginProp, pluginId: pluginIdProp, className }) => {
  const pluginId = pluginProp?.id ?? pluginIdProp ?? ""
  const { expandedPluginId, setExpandedPlugin } = useUIStore(
    useShallow((s) => ({
      expandedPluginId: s.expandedPluginId,
      setExpandedPlugin: s.setExpandedPlugin,
    }))
  )
  const { mutate: togglePlugin } = useTogglePluginMutation()
  const { mutate: deletePluginMutate, isPending: isDeleting } = useDeletePluginMutation()
  const { fadeUp } = useMotionConfig()
  // Prefer parent-passed plugin; fall back to list find for pluginId-only callers.
  const { data } = usePluginsQuery()
  const { data: healthData } = usePluginHealthQuery(pluginId)
  const plugin = useMemo(
    () => pluginProp ?? data?.plugins.find((m) => m.id === pluginId),
    [pluginProp, data, pluginId],
  )
  
  const [isRevokeOpen, setIsRevokeOpen] = useState(false)
  const [isRemoveOpen, setIsRemoveOpen] = useState(false)

  const isExpanded = expandedPluginId === pluginId
  const detailsId = `plugin-details-${pluginId}`

  if (!plugin) {
    return (
      <Card className={cn("h-[72px] animate-pulse bg-muted/50", className)}>
        <CardHeader className="pb-2" />
      </Card>
    )
  }

  const isStale = !!plugin.stale

  /**
   * Toggles the enabled state of the plugin via mutation.
   */
  const handleToggle = (checked: boolean) => {
    if (isStale) return
    togglePlugin({ id: plugin.id, enabled: checked })
  }

  const shortHash =
    plugin.content_hash && plugin.content_hash.length > 18
      ? `${plugin.content_hash.slice(0, 18)}…`
      : plugin.content_hash

  return (
    <motion.div {...fadeUp} layout>
      <Card
        className={cn(
          "transition-colors hover:bg-accent/50",
          plugin.enabled && !isStale && "border-primary/30",
          isStale && "border-destructive/40 opacity-90",
          className,
        )}
      >
        <PluginCardHeader
          plugin={plugin}
          isStale={isStale}
          isExpanded={isExpanded}
          detailsId={detailsId}
          onToggle={handleToggle}
          onRemove={() => setIsRemoveOpen(true)}
          onToggleExpand={() => setExpandedPlugin(isExpanded ? null : plugin.id)}
        />

        <AnimatePresence>
          {isExpanded && (
            <motion.div
              key="details"
              id={detailsId}
              role="region"
              aria-label={`${plugin.name} details`}
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            >
              <CardContent className="pt-0">
                <p className="text-xs text-muted-foreground">{plugin.description}</p>
                <p className="mt-1 font-mono text-xs text-muted-foreground/60">v{plugin.version}</p>
                {shortHash && (
                  <p className="mt-0.5 font-mono text-[10px] text-muted-foreground/50" title={plugin.content_hash ?? undefined}>
                    {shortHash}
                  </p>
                )}
                {isStale && (
                  <p className="mt-2 text-xs text-destructive">
                    This plugin is no longer on disk (or its proxy was removed). Remove the registry row to clean up.
                  </p>
                )}
                
                <div
                  className="mt-4 flex flex-col gap-3 border-t pt-3"
                  role="presentation"
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <PluginAuthStatus plugin={plugin} />

                    {isPluginConnected(plugin, healthData) ? (
                      <Button
                        size="sm"
                        variant="destructive"
                        className="h-8 px-3 text-xs gap-1.5 font-semibold shrink-0"
                        onClick={(e) => {
                          e.stopPropagation()
                          setIsRevokeOpen(true)
                        }}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        Revoke
                      </Button>
                    ) : (
                      <Button size="sm" variant="outline" className="h-8 px-3 text-xs gap-1.5 font-semibold shrink-0" asChild>
                        <Link
                          to="/connect"
                          search={{
                            plugin_id: plugin.id,
                            provider: plugin.external_oauth_providers?.[0] ?? "whiskers_core",
                            state: "",
                            oauth_success: "",
                          }}
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => e.stopPropagation()}
                        >
                          Connect
                        </Link>
                      </Button>
                    )}
                  </div>
                </div>

                <div className="mt-4 flex justify-end">
                  <Link
                    to="/plugins/$pluginId"
                    params={{ pluginId: pluginId }}
                    className="text-xs text-primary hover:underline"
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                  >
                    Details &rarr;
                  </Link>
                </div>
              </CardContent>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>

      <RevokeConfirmModal
        isOpen={isRevokeOpen}
        onOpenChange={setIsRevokeOpen}
        plugin={plugin}
        healthData={healthData}
      />

      <Dialog open={isRemoveOpen} onOpenChange={setIsRemoveOpen}>
        <DialogContent
          className="sm:max-w-md"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <DialogHeader>
            <DialogTitle>Remove stale plugin?</DialogTitle>
            <DialogDescription>
              Permanently delete the registry row for &quot;{plugin.name}&quot;. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:justify-end">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsRemoveOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={isDeleting}
              onClick={() => {
                deletePluginMutate(plugin.id, {
                  onSuccess: () => setIsRemoveOpen(false),
                })
              }}
            >
              {isDeleting ? "Removing…" : "Remove"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </motion.div>
  )
}

/**
 * PluginCard Component.
 * Renders the UI and handles state for the PluginCard feature.
 */
export default PluginCard
