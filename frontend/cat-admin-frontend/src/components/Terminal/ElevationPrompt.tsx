/**
 * ElevationPrompt Component
 *
 * Inline step-up elevation prompt. Renders a warm cyberpunk card asking for the
 * required verification factor(s) (TOTP 6-digit code, password, or both) to elevate
 * a terminal session. Calls the elevation store actions on verification or cancellation.
 */

import { useState } from "react"
import { useTerminalStore, type TerminalSession } from "@/store/terminalSlice"

interface ElevationPromptProps {
  session: TerminalSession
}

/**
 * Prompt card requesting TOTP and/or Password credentials to elevate a terminal session.
 */
export default function ElevationPrompt({ session }: ElevationPromptProps) {
  const [totp, setTotp] = useState("")
  const [password, setPassword] = useState("")
  const [loading, setLoading] = useState(false)

  // Only render if elevationPrompt is active.
  if (!session.elevationPrompt) {
    return null
  }

  const { elevationMethod, elevationError, id } = session
  const showTotp = elevationMethod === "totp" || elevationMethod === "both"
  const showPassword = elevationMethod === "password" || elevationMethod === "both"

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const factors: { totp?: string; password?: string } = {}
    if (showTotp && totp.trim()) {
      factors.totp = totp.trim()
    }
    if (showPassword && password.trim()) {
      factors.password = password.trim()
    }

    setLoading(true)
    try {
      await useTerminalStore.getState().submitElevation(id, factors)
    } finally {
      setLoading(false)
    }
  }

  function handleCancel() {
    useTerminalStore.getState().dismissElevation(id)
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        width: 360,
        padding: 24,
        background: "var(--bg-elevated, #1b1e1c)",
        border: "1px solid var(--amber, #f0b35b)",
        borderRadius: 10,
        boxShadow: "0 0 24px color-mix(in oklch, var(--amber, #f0b35b) 18%, transparent), inset 0 0 12px color-mix(in oklch, var(--amber, #f0b35b) 4%, transparent)",
        fontFamily: "var(--font-mono), monospace",
        color: "var(--fg, #e8e6df)",
        zIndex: 50,
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          letterSpacing: "0.1em",
          color: "var(--amber, #f0b35b)",
          marginBottom: 16,
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span>⬆</span> STEP-UP ELEVATION REQUIRED
      </div>

      <div style={{ fontSize: 11.5, color: "var(--fg-muted, #9ca3af)", marginBottom: 20, lineHeight: 1.4 }}>
        The executed command requires elevated privileges. Please verify your identity.
      </div>

      {showTotp && (
        <div style={{ marginBottom: 16 }}>
          <label
            htmlFor="elev-totp"
            style={{
              display: "block",
              fontSize: 10.5,
              color: "var(--fg-subtle, #565c58)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: 6,
            }}
          >
            TOTP 6-Digit Code
          </label>
          <input
            id="elev-totp"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={6}
            autoComplete="one-time-code"
            placeholder="000000"
            value={totp}
            onChange={(e) => setTotp(e.target.value)}
            disabled={loading}
            required
            style={{
              width: "100%",
              padding: "8px 12px",
              background: "var(--bg-sunken, #0c0f0d)",
              border: "1px solid var(--border, #2e303a)",
              borderRadius: 6,
              color: "var(--fg, #e8e6df)",
              fontSize: 13.5,
              fontFamily: "var(--font-mono), monospace",
              outline: "none",
            }}
          />
        </div>
      )}

      {showPassword && (
        <div style={{ marginBottom: 16 }}>
          <label
            htmlFor="elev-password"
            style={{
              display: "block",
              fontSize: 10.5,
              color: "var(--fg-subtle, #565c58)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: 6,
            }}
          >
            Password
          </label>
          <input
            id="elev-password"
            type="password"
            autoComplete="current-password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
            required
            style={{
              width: "100%",
              padding: "8px 12px",
              background: "var(--bg-sunken, #0c0f0d)",
              border: "1px solid var(--border, #2e303a)",
              borderRadius: 6,
              color: "var(--fg, #e8e6df)",
              fontSize: 13.5,
              fontFamily: "var(--font-mono), monospace",
              outline: "none",
            }}
          />
        </div>
      )}

      {elevationError && (
        <div
          style={{
            color: "var(--danger, #ef4444)",
            fontSize: 11,
            lineHeight: 1.4,
            marginBottom: 16,
            wordBreak: "break-word",
          }}
        >
          Error: {elevationError}
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 24 }}>
        <button
          type="button"
          onClick={handleCancel}
          disabled={loading}
          style={{
            padding: "8px 14px",
            background: "transparent",
            border: "1px solid var(--border, #2e303a)",
            borderRadius: 6,
            color: "var(--fg-muted, #9ca3af)",
            fontSize: 12,
            cursor: "pointer",
            fontFamily: "var(--font-mono), monospace",
          }}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={loading}
          style={{
            padding: "8px 16px",
            background: "linear-gradient(180deg, var(--amber-glow, #f3c788), var(--amber, #f0b35b))",
            border: "none",
            borderRadius: 6,
            color: "var(--term-bg, #0c0f0d)",
            fontSize: 12,
            fontWeight: 600,
            cursor: loading ? "not-allowed" : "pointer",
            fontFamily: "var(--font-mono), monospace",
            boxShadow: "0 0 12px color-mix(in oklch, var(--amber, #f0b35b) 30%, transparent)",
          }}
        >
          {loading ? "Verifying..." : "Verify"}
        </button>
      </div>
    </form>
  )
}
