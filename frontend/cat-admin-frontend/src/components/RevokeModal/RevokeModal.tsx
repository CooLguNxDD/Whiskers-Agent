/**
 * RevokeModal Component
 *
 * A confirmation dialog for revoking Layer 2 OAuth access.
 * Requires the user to type the provider name as a safety check before deletion.
 */

import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import revokeEmitter, { type RevokeOpenPayload } from "@/lib/revokeEvents"
import { Trash } from "@/components/shell/Icons"

type State =
  | { open: false }
  | ({ open: true; confirmText: string; revoking: boolean; error: string | null } & RevokeOpenPayload)

const INITIAL: State = { open: false }

/**
 * Renders a global portal-based modal for OAuth revocation confirmation.
 */
export default function RevokeModal() {
  const [state, setState] = useState<State>(INITIAL)

  useEffect(() => {
    function handleOpen(payload: RevokeOpenPayload) {
      setState({ open: true, confirmText: "", revoking: false, error: null, ...payload })
    }
    revokeEmitter.on("revoke:open", handleOpen)
    return () => revokeEmitter.off("revoke:open", handleOpen)
  }, [])

  useEffect(() => {
    if (!state.open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [state.open])

  function close() {
    setState(INITIAL)
  }

  async function handleConfirm() {
    if (!state.open) return
    if (state.confirmText !== state.provider) return
    setState((s) => ({ ...s, revoking: true, error: null }))
    try {
      await state.onConfirm(state.pluginId, state.provider)
      close()
    } catch {
      setState((s) => ({ ...s, revoking: false, error: "Revoke failed — please try again." }))
    }
  }

  if (!state.open) return null

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="revoke-modal-title"
      style={{
        position: "fixed", inset: 0, zIndex: 9999,
        display: "grid", placeItems: "center",
        padding: 24,
        background: "color-mix(in oklch, var(--bg-sunken, oklch(0.14 0.015 42)) 70%, transparent)",
        backdropFilter: "blur(6px)",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) close() }}
    >
      <div
        className="ct-modal"
        style={{ animation: "ct-modal-in 140ms cubic-bezier(0.16,1,0.3,1)" }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <div className="ct-modal-head">
          <div className="ct-modal-icon">
            <Trash width="18" height="18" />
          </div>
          <div>
            <div className="ct-modal-title" id="revoke-modal-title">Revoke OAuth access?</div>
            <div className="ct-modal-sub">
              This will permanently delete the stored token for{" "}
              <strong>{state.provider}</strong>. The plugin will lose access until you reconnect.
            </div>
          </div>
        </div>

        <div className="ct-modal-body">
          <div style={{ fontSize: 12.5, color: "var(--fg-muted)", marginBottom: 4 }}>
            Type{" "}
            <code
              style={{
                fontFamily: "var(--font-mono)", background: "var(--bg-sunken)",
                border: "1px solid var(--hairline)", borderRadius: 4,
                padding: "1px 6px", fontSize: 12, color: "var(--fg)",
              }}
            >
              {state.provider}
            </code>{" "}
            to confirm:
          </div>
          <input
            autoFocus
            className="ct-confirm-input"
            placeholder={state.provider}
            value={state.confirmText}
            onChange={(e) =>
              setState((s) => ({ ...s, confirmText: e.target.value, error: null }))
            }
            onKeyDown={(e) => { if (e.key === "Enter") void handleConfirm() }}
            disabled={state.revoking}
          />
          {state.error && (
            <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 8, fontFamily: "var(--font-mono)" }}>
              {state.error}
            </div>
          )}
        </div>

        <div className="ct-modal-foot">
          <button className="ct-btn-ghost" onClick={close} disabled={state.revoking}>
            Cancel
          </button>
          <button
            className="ct-btn-danger-solid"
            disabled={state.confirmText !== state.provider || state.revoking}
            onClick={() => void handleConfirm()}
          >
            {state.revoking ? "Revoking…" : "Revoke access"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
