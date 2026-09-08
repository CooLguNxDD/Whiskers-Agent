/**
 * TokenRevealBanner — one-time secret display after key creation or rotation.
 * Shows the raw token with copy + dismiss; never re-fetched once dismissed.
 */

import { useRef } from "react"
import { AlertTriangle } from "lucide-react"
import { useClipboard } from "@/hooks/useClipboard"

interface TokenRevealBannerProps {
  token: string
  onDismiss: () => void
}

/** Banner that reveals a freshly minted API key token once. */
export function TokenRevealBanner({ token, onDismiss }: TokenRevealBannerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const { copied, copy } = useClipboard(textareaRef)

  const handleCopy = () => {
    void copy(token)
  }

  return (
    <div className="ct-panel mx-auto max-w-[60%] bg-[color-mix(in_oklch,var(--amber)_5%,transparent)] border border-[color-mix(in_oklch,var(--amber)_25%,var(--hairline))] rounded-xl shadow-sm p-4 flex flex-col gap-3 mb-4">
      <div className="flex gap-2 items-center text-[var(--amber)] font-bold text-xs">
        <AlertTriangle className="size-4 shrink-0" />
        <span>Copy your API key now — it will not be shown again.</span>
      </div>
      <textarea
        ref={textareaRef}
        readOnly
        value={token}
        aria-label="Generated API key token"
        onClick={(e) => (e.target as HTMLTextAreaElement).select()}
        className="w-full h-16 font-mono text-[11px] bg-[color-mix(in_oklch,var(--fg)_8%,transparent)] border border-border rounded p-2 text-foreground resize-none break-all"
      />
      <div className="flex justify-between items-center mt-1">
        <button type="button" className="ct-btn-ghost text-xs py-1 px-3" onClick={onDismiss}>
          Done
        </button>
        <button type="button" className="ct-btn-primary text-xs py-1 px-4" onClick={handleCopy}>
          {copied ? "Copied!" : "Copy Key"}
        </button>
      </div>
    </div>
  )
}

export default TokenRevealBanner
