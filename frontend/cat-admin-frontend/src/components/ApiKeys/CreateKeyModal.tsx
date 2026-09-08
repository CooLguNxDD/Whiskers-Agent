import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"

export interface CreateKeyModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (name: string, expiresInSecs?: number) => void
  isPending: boolean
  error?: string | null
}

/**
 * Modal for creating a new API key. Handles naming and preset selection.
 */
export function CreateKeyModal({
  open,
  onOpenChange,
  onSubmit,
  isPending,
  error,
}: CreateKeyModalProps) {
  const [name, setName] = useState("")
  const [expiresOption, setExpiresOption] = useState("never")

  const [prevOpen, setPrevOpen] = useState(open)
  if (open !== prevOpen) {
    setPrevOpen(open)
    if (!open) {
      setName("")
      setExpiresOption("never")
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    const secs = expiresOption === "never" ? undefined : parseInt(expiresOption, 10)
    onSubmit(name.trim(), secs)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md bg-[var(--card-soft)] border border-border rounded-xl shadow-lg p-6">
        <DialogHeader>
          <DialogTitle className="text-sm font-bold text-foreground font-sans">
            Create API Key
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground mt-1 font-sans">
            Generate a new credential to access the Whiskers Agent server.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 mt-3">
          <div className="space-y-1">
            <label htmlFor="modal-key-name" className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider block">
              Name / Description
            </label>
            <input
              id="modal-key-name"
              type="text"
              required
              placeholder="e.g. VS Code integration"
              className="w-full ct-input text-xs"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isPending}
            />
          </div>

          <div className="space-y-1">
            <label htmlFor="modal-key-expiry" className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider block">
              Expiration
            </label>
            <select
              id="modal-key-expiry"
              className="w-full ct-input text-xs"
              value={expiresOption}
              onChange={(e) => setExpiresOption(e.target.value)}
              disabled={isPending}
            >
              <option value="never">Never</option>
              <option value="3600">1 Hour</option>
              <option value="86400">1 Day</option>
              <option value="604800">7 Days</option>
              <option value="2592000">30 Days</option>
              <option value="7776000">90 Days</option>
            </select>
          </div>

          <div className="bg-[color-mix(in_oklch,var(--amber)_10%,transparent)] border border-[color-mix(in_oklch,var(--amber)_22%,var(--hairline))] text-[var(--amber)] text-[11px] rounded p-2.5 font-sans leading-relaxed block">
            New keys start with <span className="font-mono text-[10px] bg-[color-mix(in_oklch,var(--amber)_12%,transparent)] px-1.5 py-0.5 rounded text-[var(--amber)] border border-[color-mix(in_oklch,var(--amber)_20%,var(--hairline))] inline-block align-middle mx-0.5">[]</span> empty scopes for security. Configure permissions after creation.
          </div>

          {error && (
            <div className="p-2 text-xs bg-[var(--danger-soft)] text-[var(--danger)] rounded border border-[color-mix(in_oklch,var(--danger)_28%,var(--hairline))] font-medium">
              {error}
            </div>
          )}

          <DialogFooter className="flex flex-col sm:flex-row gap-2 sm:justify-end mt-4">
            <button
              type="button"
              className="ct-btn-ghost text-xs py-1.5 px-4 font-sans"
              onClick={() => onOpenChange(false)}
              disabled={isPending}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="ct-btn-primary text-xs py-1.5 px-4 font-sans"
              disabled={isPending}
            >
              {isPending ? "Generating..." : "Generate Key"}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default CreateKeyModal
