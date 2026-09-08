/**
 * HostTokenButton Component
 *
 * Renders a button to generate and copy terminal host tokens (Layer-1 terminal:host JWT)
 * used to authenticate the VS Code extension connection.
 */

import { useState, useRef, useEffect } from "react"
import { generateHostToken } from "@/api/terminal"

/**
 * Component to generate and display the VS Code extension token.
 */
export default function HostTokenButton() {
  const [isPending, setIsPending] = useState(false)
  const [token, setToken] = useState<string | null>(null)
  const [expiresAt, setExpiresAt] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Use a timer to update the remaining expiry text every second if token is visible
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!expiresAt) return
    const timer = setInterval(() => {
      setNow(Date.now())
    }, 1000)
    return () => clearInterval(timer)
  }, [expiresAt])

  /** Format remaining duration until expiry and absolute expiration time. */
  function formatExpiry(expiryStr: string | null): string {
    if (!expiryStr) return ""
    const date = new Date(expiryStr)
    const diffMs = date.getTime() - now
    const diffMins = Math.round(diffMs / 60000)
    const absTime = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })

    if (diffMs <= 0) {
      return `expired at ${absTime}`
    }
    if (diffMins < 1) {
      const diffSecs = Math.max(0, Math.round(diffMs / 1000))
      return `in ${diffSecs}s (${absTime})`
    }
    if (diffMins < 60) {
      return `in ${diffMins}m (${absTime})`
    }
    const diffHours = Math.round(diffMins / 60)
    return `in ${diffHours}h (${absTime})`
  }

  /** Trigger token generation request. */
  const handleGenerate = async () => {
    setIsPending(true)
    setError(null)
    setToken(null)
    setExpiresAt(null)

    try {
      const res = await generateHostToken()
      setToken(res.token)
      setExpiresAt(res.expires_at)
    } catch (err) {
      let errorMsg = "An unexpected error occurred."
      if (err instanceof Error) {
        if (err.message === "Unauthorized") {
          errorMsg = "Session expired — reload and log in."
        } else if (err.message.includes("503")) {
          errorMsg = "OAuth is disabled on this server."
        } else {
          errorMsg = err.message
        }
      }
      setError(errorMsg)
    } finally {
      setIsPending(false)
    }
  }

  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  /** Copy the token to the user's clipboard, falling back to selecting the box. */
  const handleCopy = async () => {
    if (!token) return

    // Guard clipboard API use in insecure contexts
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(token)
        setCopied(true)
        if (timeoutRef.current) clearTimeout(timeoutRef.current)
        timeoutRef.current = setTimeout(() => setCopied(false), 2000)
        return
      } catch (err) {
        // Log clipboard API failure and proceed to selection fallback
        console.error("Clipboard API failed, trying fallback select copy", err)
      }
    }

    // Fallback: select textarea content and run document command
    if (textareaRef.current) {
      textareaRef.current.select()
      try {
        document.execCommand("copy")
        setCopied(true)
        if (timeoutRef.current) clearTimeout(timeoutRef.current)
        timeoutRef.current = setTimeout(() => setCopied(false), 2000)
      } catch (err) {
        console.error("Fallback copy execution failed", err)
        setError("Failed to copy to clipboard — please select and copy manually.")
      }
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-end", maxWidth: "100%" }}>
      <button
        className="ct-btn-ghost"
        onClick={handleGenerate}
        disabled={isPending}
        style={{ padding: "6px 10px", fontSize: 13 }}
      >
        {isPending ? "Generating…" : "Generate Extension Token"}
      </button>

      {error && (
        <div style={{ color: "var(--warn)", fontSize: 12, marginTop: 4, fontFamily: "var(--font-mono)" }}>
          {error}
        </div>
      )}

      {token && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            width: "360px",
            maxWidth: "100%",
            padding: "10px",
            background: "var(--bg-sunken)",
            border: "1px solid var(--border)",
            borderRadius: "6px",
            marginTop: 4,
          }}
        >
          <textarea
            ref={textareaRef}
            readOnly
            value={token}
            aria-label="Generated host token"
            style={{
              width: "100%",
              height: "70px",
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
              background: "color-mix(in oklch, var(--fg, #e8e6df) 8%, transparent)",
              border: "1px solid var(--border)",
              borderRadius: "4px",
              padding: "6px",
              color: "var(--fg)",
              resize: "none",
              wordBreak: "break-all",
              whiteSpace: "pre-wrap",
            }}
          />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "11px", color: "var(--fg-muted)" }}>
              Paste into VS Code: Whiskers Agent → paste at the prompt. Expires {formatExpiry(expiresAt)}.
            </span>
            <button
              className="ct-btn-ghost"
              onClick={handleCopy}
              style={{ padding: "4px 8px", fontSize: "11px", flexShrink: 0, marginLeft: 8 }}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
