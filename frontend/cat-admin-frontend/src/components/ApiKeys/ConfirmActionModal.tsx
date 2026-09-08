/**
 * ConfirmActionModal — confirmation dialog for revoking or deleting an API key.
 */

import { AlertTriangle } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import type { ApiKey } from "@/api/apiKeys"

export interface ConfirmActionModalProps {
  action: { type: "revoke" | "delete"; key: ApiKey } | null
  onConfirm: () => void
  onClose: () => void
  isPending: boolean
}

/**
 * A reusable confirmation modal for sensitive actions like rotation or deletion.
 */
export function ConfirmActionModal({
  action,
  onConfirm,
  onClose,
  isPending,
}: ConfirmActionModalProps) {
  return (
    <Dialog open={!!action} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md bg-[var(--card-soft)] border border-border rounded-xl shadow-lg p-6">
        <DialogHeader className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-[var(--danger)]">
            <AlertTriangle className="size-5 shrink-0" />
            <DialogTitle className="text-sm font-bold tracking-tight">
              {action?.type === "revoke" ? "Revoke & Replace API Key?" : "Delete API Key?"}
            </DialogTitle>
          </div>
          <DialogDescription className="text-xs text-muted-foreground font-sans">
            {action?.type === "revoke"
              ? `Revoking will disable the API key "${action?.key?.name}" immediately. A replacement key with the same name will be generated and its secret shown only once.`
              : `Are you sure you want to permanently delete the API key "${action?.key?.name}"? This action is irreversible.`}
          </DialogDescription>
        </DialogHeader>

        <div className="my-3 rounded-lg border border-border bg-[color-mix(in_oklch,var(--fg)_8%,transparent)] p-3 text-[10px] font-mono">
          <div className="flex justify-between py-1">
            <span className="font-semibold text-muted-foreground">Name</span>
            <span className="text-foreground">{action?.key?.name}</span>
          </div>
          <div className="flex justify-between py-1 border-t border-hairline mt-1 pt-1">
            <span className="font-semibold text-muted-foreground">Key Prefix</span>
            <span className="text-foreground">{action?.key?.prefix}…</span>
          </div>
        </div>

        <DialogFooter className="flex flex-col sm:flex-row gap-2 sm:justify-end">
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs font-semibold font-sans"
            disabled={isPending}
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="h-8 text-xs font-semibold px-4 font-sans"
            disabled={isPending}
            onClick={onConfirm}
          >
            {isPending
              ? action?.type === "revoke"
                ? "Replacing..."
                : "Deleting..."
              : action?.type === "revoke"
              ? "Revoke & Replace"
              : "Delete key"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default ConfirmActionModal
