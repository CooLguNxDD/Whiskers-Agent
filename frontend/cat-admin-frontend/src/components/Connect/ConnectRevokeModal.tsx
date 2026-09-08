/**
 * ConnectRevokeModal Component
 *
 * Typed-confirm revoke dialog for the Connect screen's active method (OAuth
 * token or Direct Login credentials), matching the ct-modal design pattern.
 */
import { useEffect, useRef, useState, type FC } from "react"
import { createPortal } from "react-dom"
import { Lock } from "@/components/shell/Icons"
import type { ProviderMeta } from "./providers"

export interface ConnectRevokeModalProps {
  open: boolean
  isOauth: boolean
  basicMethod: "password" | "token"
  providerMeta: ProviderMeta
  pluginId: string
  onCancel: () => void
  onConfirm: () => Promise<void>
}

/**
 * ConnectRevokeModal Component.
 * Renders the typed-confirm revoke dialog gating the danger action on typing
 * the provider's display name.
 */
const ConnectRevokeModal: FC<ConnectRevokeModalProps> = ({
  open,
  isOauth,
  basicMethod,
  providerMeta,
  pluginId,
  onCancel,
  onConfirm,
}) => {
  const [confirmText, setConfirmText] = useState("")
  const [revoking, setRevoking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onCancelRef = useRef(onCancel)
  const modalRef = useRef<HTMLDivElement | null>(null)
  const prevActiveElementRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    onCancelRef.current = onCancel
  }, [onCancel])

  useEffect(() => {
    if (open) {
      prevActiveElementRef.current = document.activeElement as HTMLElement | null
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setConfirmText("")
      setRevoking(false)
      setError(null)
    }
  }, [open])

  useEffect(() => {
    if (!open) return

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault()
        onCancelRef.current()
        return
      }

      if (e.key === "Tab") {
        if (!modalRef.current) return
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
        if (focusable.length === 0) {
          e.preventDefault()
          return
        }

        const first = focusable[0]
        const last = focusable[focusable.length - 1]

        if (e.shiftKey) {
          if (document.activeElement === first || !modalRef.current.contains(document.activeElement)) {
            e.preventDefault()
            last.focus()
          }
        } else {
          if (document.activeElement === last || !modalRef.current.contains(document.activeElement)) {
            e.preventDefault()
            first.focus()
          }
        }
      }
    }

    window.addEventListener("keydown", onKeyDown)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
      if (prevActiveElementRef.current && typeof prevActiveElementRef.current.focus === "function") {
        prevActiveElementRef.current.focus()
      }
    }
  }, [open])

  if (!open) return null

  const phrase = providerMeta.name.toLowerCase()
  const matches = confirmText.trim().toLowerCase() === phrase

  async function handleConfirm() {
    if (!matches) return
    setRevoking(true)
    setError(null)
    try {
      await onConfirm()
    } catch {
      setRevoking(false)
      setError("Revoke failed — please try again.")
    }
  }

  return createPortal(
    <div
      ref={modalRef}
      className="ct-modal-scrim"
      role="dialog"
      aria-modal="true"
      aria-labelledby="connect-revoke-title"
      style={{ position: "fixed" }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div
        className="ct-modal"
        style={{ animation: "ct-modal-in 140ms cubic-bezier(0.16,1,0.3,1)" }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <div className="ct-modal-head">
          <div className="ct-modal-icon">
            <Lock width="18" height="18" />
          </div>
          <div>
            <div className="ct-modal-title" id="connect-revoke-title">
              Revoke {isOauth ? "OAuth token" : "stored credentials"}?
            </div>
            <div className="ct-modal-sub">
              The plugin will lose access to{" "}
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>{providerMeta.handle}</span> immediately.
              In-flight tool calls will fail until reconnected.
            </div>
          </div>
        </div>
        <div className="ct-modal-body">
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 4,
              padding: "10px 12px",
              background: "var(--bg-sunken)",
              border: "1px solid var(--hairline)",
              borderRadius: 8,
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: "var(--fg-muted)",
            }}
          >
            <div>
              <span style={{ color: "var(--fg-subtle)" }}>method</span> &nbsp;{" "}
              {isOauth ? "oauth · layer 2" : `direct · ${basicMethod}`}
            </div>
            <div>
              <span style={{ color: "var(--fg-subtle)" }}>scope</span> &nbsp;{" "}
              {providerMeta.scopes.length > 0 ? providerMeta.scopes.join(" ") : "—"}
            </div>
            <div>
              <span style={{ color: "var(--fg-subtle)" }}>holder</span> &nbsp; {pluginId}
            </div>
            <div>
              <span style={{ color: "var(--fg-subtle)" }}>provider</span>&nbsp; {providerMeta.handle}
            </div>
          </div>
          <div style={{ marginTop: 14, fontSize: 12.5, color: "var(--fg-muted)" }}>
            Type <code>{phrase}</code> to confirm.
          </div>
          <input
            className="ct-confirm-input"
            placeholder={phrase}
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleConfirm()
            }}
            disabled={revoking}
            autoFocus
          />
          {error && (
            <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 8, fontFamily: "var(--font-mono)" }}>
              {error}
            </div>
          )}
        </div>
        <div className="ct-modal-foot">
          <button type="button" className="ct-btn-ghost" onClick={onCancel} disabled={revoking}>
            Cancel
          </button>
          <button
            type="button"
            className="ct-btn-danger-solid"
            disabled={!matches || revoking}
            onClick={() => void handleConfirm()}
          >
            <Lock width="12" height="12" /> {revoking ? "Revoking…" : `Revoke ${isOauth ? "token" : "credentials"}`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

export default ConnectRevokeModal
