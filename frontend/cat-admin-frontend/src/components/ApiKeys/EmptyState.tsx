/**
 * EmptyState — shown on the API Keys dashboard when no keys exist yet.
 */

import { Key as KeyIcon } from "lucide-react"
import { Button } from "@/components/ui/button"

interface EmptyStateProps {
  onCreateClick: () => void
}

/**
 * Displays an empty state when no API keys are available.
 * Properties: onCreateClick - callback when create button is clicked.
 * Re-render notes: Stateless component, only re-renders when props change.
 */
export function EmptyState({ onCreateClick }: EmptyStateProps) {
  return (
    <div className="max-w-md mx-auto text-center ct-panel bg-card border border-border rounded-xl p-10 mt-8 flex flex-col items-center justify-center gap-4">
      <div className="p-3 ct-tag-amber rounded-full">
        <KeyIcon className="size-8" />
      </div>
      <div>
        <h3 className="text-sm font-bold text-foreground">No API keys configured yet</h3>
        <p className="text-xs text-muted-foreground mt-2 max-w-xs leading-relaxed">
          Generate an API key to allow external integrations or client applications to communicate with the Whiskers Agent server.
        </p>
      </div>
      <Button
        type="button"
        variant="default"
        onClick={onCreateClick}
        className="ct-btn-primary text-xs font-bold font-sans h-9 px-5 mt-2"
      >
        Create API Key
      </Button>
    </div>
  )
}

export default EmptyState
