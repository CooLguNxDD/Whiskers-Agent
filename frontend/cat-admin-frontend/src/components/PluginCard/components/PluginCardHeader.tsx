import type { FC, KeyboardEvent, MouseEvent } from "react"
import { CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import type { Plugin } from "@/types/plugin"

export interface PluginCardHeaderProps {
  plugin: Plugin
  isStale: boolean
  isExpanded?: boolean
  detailsId?: string
  onToggle: (checked: boolean) => void
  onRemove: () => void
  onToggleExpand?: () => void
}

/**
 * Header section for a plugin card.
 */
export const PluginCardHeader: FC<PluginCardHeaderProps> = ({
  plugin,
  isStale,
  isExpanded = false,
  detailsId,
  onToggle,
  onRemove,
  onToggleExpand,
}) => {
  const stopProp = (e: MouseEvent | KeyboardEvent) => e.stopPropagation()
  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.stopPropagation()
    }
  }

  return (
    <CardHeader className="flex flex-row items-center justify-between pb-2">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="flex items-center gap-2 text-left bg-transparent p-0 border-0 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
          aria-expanded={isExpanded}
          aria-controls={detailsId}
          onClick={(e) => {
            e.stopPropagation()
            onToggleExpand?.()
          }}
        >
          <CardTitle className="text-sm font-medium hover:underline">{plugin.name}</CardTitle>
        </button>
        <Badge variant={plugin.tier === "pro" ? "default" : "secondary"} className="text-xs">
          {plugin.tier}
        </Badge>
        {isStale && (
          <Badge variant="destructive" className="text-xs" aria-label="Plugin is stale: no longer present on disk">
            Stale
          </Badge>
        )}
      </div>
      <div
        className="flex items-center gap-2"
        onClick={stopProp}
        onKeyDown={handleKeyDown}
      >
        {isStale && (
          <Button
            size="sm"
            variant="destructive"
            className="h-7 px-2 text-xs"
            onClick={onRemove}
          >
            Remove
          </Button>
        )}
        <Switch
          className="ct-switch"
          checked={plugin.enabled}
          onCheckedChange={onToggle}
          disabled={isStale}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.stopPropagation()
            }
          }}
          aria-label={`Toggle ${plugin.name}`}
        />
      </div>
    </CardHeader>
  )
}

