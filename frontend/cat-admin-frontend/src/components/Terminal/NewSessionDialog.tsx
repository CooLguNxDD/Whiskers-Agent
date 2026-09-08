/**
 * NewSessionDialog Component
 *
 * Modal for spawning a new terminal session: pick an online IDE host and an optional
 * working directory, then open a relay session. If exactly one host is online it is
 * preselected. Closes on success or cancel.
 */

import { useEffect, useState, useMemo } from "react"
import { useTerminalHosts } from "@/hooks/useTerminal"
import { useTerminalStore } from "@/store/terminalSlice"
import { getErrorMessage } from "@/utils/errors"

interface NewSessionDialogProps {
  open: boolean
  onClose: () => void
}

/**
 * Renders the new-session host picker dialog.
 */
export default function NewSessionDialog({ open, onClose }: NewSessionDialogProps) {
  const { data, isPending } = useTerminalHosts()
  const openSession = useTerminalStore((s) => s.openSession)
  const hosts = useMemo(() => data?.hosts ?? [], [data?.hosts])

  const [ideId, setIdeId] = useState("")
  const [workdir, setWorkdir] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Preselect the only online host.
  useEffect(() => {
    if (hosts.length === 1) {
      const targetId = hosts[0].ide_id
      const timer = setTimeout(() => {
        setIdeId(targetId)
      }, 0)
      return () => clearTimeout(timer)
    }
  }, [hosts])

  if (!open) return null

  async function spawn() {
    if (!ideId) { setError("Select an IDE host."); return }
    // eslint-disable-next-line no-control-regex
    if (/[\x00-\x1f]/.test(workdir.trim())) {
      setError("Working directory contains invalid characters.")
      return
    }
    setBusy(true)
    setError(null)
    try {
      await openSession(ideId, workdir.trim() || undefined)
      setWorkdir("")
      onClose()
    } catch (e) {
      setError(getErrorMessage(e, "Failed to open session"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-session-title"
      style={{
        position: "fixed", inset: 0, zIndex: 50, display: "grid", placeItems: "center",
        background: "color-mix(in oklch, var(--bg) 60%, transparent)", backdropFilter: "blur(2px)",
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
        style={{
          width: 420, maxWidth: "90vw", padding: 20, borderRadius: 12,
          background: "var(--card)", border: "1px solid var(--border)",
          boxShadow: "0 0 40px -12px color-mix(in oklch, var(--amber) 40%, transparent)",
        }}
      >
        <div id="new-session-title" className="ct-section-title" style={{ marginBottom: 12 }}>New terminal session</div>

        <label className="ct-eyebrow" style={{ display: "block", marginBottom: 6 }}>IDE host</label>
        {isPending ? (
          <div style={{ color: "var(--fg-muted)", fontSize: 13 }}>Loading hosts…</div>
        ) : hosts.length === 0 ? (
          <div style={{ color: "var(--warn, #d98a3b)", fontSize: 13 }}>
            No IDE hosts online. Start the Whiskers Agent VS Code extension.
          </div>
        ) : (
          <select
            value={ideId}
            onChange={(e) => setIdeId(e.target.value)}
            style={{
              width: "100%", padding: "8px 10px", borderRadius: 8, marginBottom: 14,
              background: "var(--bg-sunken)", color: "var(--fg)", border: "1px solid var(--border)",
              fontFamily: "var(--font-mono)", fontSize: 13,
            }}
          >
            <option value="">Select a host…</option>
            {hosts.map((h) => (
              <option key={h.ide_id} value={h.ide_id}>{h.ide_id}</option>
            ))}
          </select>
        )}

        <label className="ct-eyebrow" style={{ display: "block", marginBottom: 6 }}>
          Working directory <span style={{ opacity: 0.6 }}>(optional)</span>
        </label>
        <input
          value={workdir}
          onChange={(e) => setWorkdir(e.target.value)}
          placeholder="/path/to/repo"
          style={{
            width: "100%", padding: "8px 10px", borderRadius: 8, marginBottom: 14,
            background: "var(--bg-sunken)", color: "var(--fg)", border: "1px solid var(--border)",
            fontFamily: "var(--font-mono)", fontSize: 13,
          }}
        />

        {error && <div style={{ color: "var(--warn, #d98a3b)", fontSize: 12.5, marginBottom: 10 }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="ct-btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="ct-btn-primary" onClick={() => void spawn()} disabled={busy || !ideId}>
            {busy ? "Opening…" : "Open terminal"}
          </button>
        </div>
      </div>
    </div>
  )
}
