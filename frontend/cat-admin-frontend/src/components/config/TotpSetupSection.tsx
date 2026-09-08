import { useState, type FC } from "react"
import { useNavigate, useSearch } from "@tanstack/react-router"
import { useTotpStatusQuery } from "@/hooks/useTotp"
import { useQueryClient } from "@tanstack/react-query"
import { QRCodeSVG } from "qrcode.react"
import { provisionTotp, verifyTotp } from "@/api/terminal"
import { ShieldCheck, ShieldAlert, Key, Copy, Check, Loader2 } from "lucide-react"
import { getErrorMessage } from "@/utils/errors"

const TotpSetupSection: FC = () => {
  const { data: totpData } = useTotpStatusQuery()
  const totpProvisioned = totpData?.totp_provisioned
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const search = useSearch({ from: "/config" })
  const returnTo = search.returnTo
  const goTerminal = () => {
    void navigate({ to: "/terminal" })
  }

  const [initiating, setInitiating] = useState(false)
  const [otpauthUri, setOtpauthUri] = useState<string | null>(null)
  const [secretKey, setSecretKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const [verifyCode, setVerifyCode] = useState("")
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  // Start the setup flow and fetch a pending seed
  const handleBeginSetup = async () => {
    setInitiating(true)
    setError(null)
    try {
      const res = await provisionTotp()
      if (res.otpauth_uri) {
        setOtpauthUri(res.otpauth_uri)
        // Extract the secret key parameter for manual entry
        try {
          const url = new URL(res.otpauth_uri)
          const secret = url.searchParams.get("secret")
          setSecretKey(secret || null)
        } catch {
          setSecretKey(null)
        }
      } else {
        setError("Failed to generate provisioning URI.")
      }
    } catch (err) {
      setError(getErrorMessage(err, "Failed to initiate TOTP provisioning."))
    } finally {
      setInitiating(false)
    }
  }

  // Copy secret key to clipboard
  const handleCopySecret = () => {
    if (!secretKey) return
    navigator.clipboard.writeText(secretKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Verify the 6-digit code against the pending seed
  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!verifyCode.trim()) return
    setVerifying(true)
    setError(null)
    try {
      const res = await verifyTotp(verifyCode.trim())
      if (res.status === "ok") {
        setSuccess(true)
        // Delay update slightly to let the user see the success state
        setTimeout(() => {
          void queryClient.invalidateQueries({ queryKey: ["totp_status"] })
          if (returnTo === "/terminal") {
            goTerminal()
          }
        }, 1500)
      } else {
        setError("Failed to verify code.")
      }
    } catch (err) {
      setError(getErrorMessage(err, "Invalid verification code. Please try again."))
    } finally {
      setVerifying(false)
    }
  }

  // Active status display
  if (totpProvisioned) {
    return (
      <div className="ct-form-section">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", padding: "24px 12px" }}>
          <div className="ct-status-badge active" style={{ display: "flex", padding: 12, borderRadius: "50%", background: "var(--ok-soft)", color: "var(--ok)", marginBottom: 16 }}>
            <ShieldCheck size={48} />
          </div>
          <h3 style={{ margin: "0 0 8px 0", fontSize: 22, color: "var(--fg)" }}>
            TOTP Step-Up Active
          </h3>
          <p className="sub" style={{ maxWidth: 460, margin: "0 0 24px 0", color: "var(--fg-muted)" }}>
            Time-based One-Time Passwords are fully configured. Any privileged command attempts or VS Code terminal write operations will require a 6-digit verification code.
          </p>
          <div style={{ padding: "12px 18px", borderRadius: "var(--radius)", background: "var(--bg-sunken)", border: "1px solid var(--hairline)", width: "100%", maxWidth: 460 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
              <span style={{ color: "var(--fg-subtle)" }}>Status</span>
              <span className="ct-pill" style={{ color: "var(--ok)", borderColor: "var(--ok)" }}>SECURE</span>
            </div>
            <div style={{ borderTop: "1px solid var(--hairline)", margin: "8px 0", paddingTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13 }}>
              <span style={{ color: "var(--fg-subtle)" }}>Reset configuration</span>
              <span style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>Use Danger Zone vault reset</span>
            </div>
          </div>
          {returnTo === "/terminal" ? (
            <button
              type="button"
              className="ct-btn-primary"
              style={{ marginTop: 20 }}
              onClick={goTerminal}
            >
              Continue to Terminal
            </button>
          ) : null}
        </div>
      </div>
    )
  }

  // Enrollment Wizard
  return (
    <div className="ct-form-section">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>Step-Up TOTP Setup</h3>
        <span className="ct-pill" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>REQUIRED</span>
      </div>
      <p className="sub">
        Configure a Time-based One-Time Password (TOTP) to secure write permissions and critical command elevation.
        {returnTo === "/terminal" ? " After verify you will return to the Terminal." : ""}
      </p>

      {error && (
        <div style={{ display: "flex", gap: 12, padding: "12px 16px", background: "var(--danger-soft)", color: "var(--danger)", border: "1px solid var(--danger)", borderRadius: "var(--radius)", margin: "16px 0", fontSize: 14 }}>
          <ShieldAlert size={20} style={{ flexShrink: 0 }} />
          <div>{error}</div>
        </div>
      )}

      {success && (
        <div style={{ display: "flex", gap: 12, padding: "12px 16px", background: "var(--ok-soft)", color: "var(--ok)", border: "1px solid var(--ok)", borderRadius: "var(--radius)", margin: "16px 0", fontSize: 14 }}>
          <ShieldCheck size={20} style={{ flexShrink: 0 }} />
          <div>TOTP configured successfully! Activating configuration and unlocking application...</div>
        </div>
      )}

      {!otpauthUri ? (
        <div style={{ padding: "32px 16px", textAlign: "center", border: "1px dashed var(--border)", borderRadius: "var(--radius)", background: "var(--bg-sunken)", marginTop: 16 }}>
          <div style={{ display: "inline-flex", padding: 12, borderRadius: "50%", background: "var(--warn-soft)", color: "var(--warn)", marginBottom: 16 }}>
            <Key size={32} />
          </div>
          <h4 style={{ margin: "0 0 8px 0", color: "var(--fg)" }}>Setup Authenticator App</h4>
          <p style={{ fontSize: 14, color: "var(--fg-muted)", maxWidth: 440, margin: "0 auto 24px auto" }}>
            Get a 6-digit verification code from your authenticator app (Google Authenticator, Authy, or 1Password) to elevate terminal actions. Click below to begin.
          </p>
          <button
            className="ct-btn-primary"
            onClick={handleBeginSetup}
            disabled={initiating}
            style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
          >
            {initiating && <Loader2 size={16} className="animate-spin" />}
            Generate Security Key
          </button>
        </div>
      ) : (
        <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 24 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "flex-start" }}>
            {/* QR Code Container */}
            <div className="ct-qr-surface" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, padding: 16, width: 192, height: 192, justifyContent: "center" }}>
              <QRCodeSVG value={otpauthUri} size={160} />
            </div>

            {/* Steps & Manual Key */}
            <div style={{ flex: 1, minWidth: 280, display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <h4 style={{ margin: "0 0 4px 0", color: "var(--fg)", fontSize: 16 }}>1. Scan QR Code</h4>
                <p style={{ fontSize: 13, color: "var(--fg-muted)" }}>
                  Open your authenticator app, tap add/scan, and scan the QR code to register this server.
                </p>
              </div>

              {secretKey && (
                <div>
                  <h4 style={{ margin: "0 0 4px 0", color: "var(--fg)", fontSize: 16 }}>Or Enter Key Manually</h4>
                  <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                    <code style={{ flex: 1, padding: "8px 12px", background: "var(--bg-sunken)", border: "1px solid var(--border)", fontSize: 13, overflowX: "auto", display: "block", whiteSpace: "nowrap" }}>
                      {secretKey}
                    </code>
                    <button
                      className="ct-btn-ghost"
                      onClick={handleCopySecret}
                      style={{ padding: "8px 12px", display: "flex", alignItems: "center", justifyContent: "center" }}
                      title="Copy security key"
                    >
                      {copied ? <Check size={16} style={{ color: "var(--ok)" }} /> : <Copy size={16} />}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Verification Code Form */}
          <form onSubmit={handleVerify} style={{ borderTop: "1px solid var(--hairline)", paddingTop: 20 }}>
            <h4 style={{ margin: "0 0 8px 0", color: "var(--fg)", fontSize: 16 }}>2. Enter Verification Code</h4>
            <p style={{ fontSize: 13, color: "var(--fg-muted)", marginBottom: 12 }}>
              Input the 6-digit code displayed in your authenticator app to complete the configuration.
            </p>
            <div style={{ display: "flex", gap: 12, maxWidth: 360 }}>
              <input
                className="ct-input"
                placeholder="000000"
                value={verifyCode}
                onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                disabled={verifying || success}
                style={{ fontSize: 20, letterSpacing: "0.2em", textAlign: "center", fontFamily: "var(--font-mono)" }}
                required
              />
              <button
                type="submit"
                className="ct-btn-primary"
                disabled={verifying || success || verifyCode.length < 6}
                style={{ display: "inline-flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}
              >
                {verifying && <Loader2 size={16} className="animate-spin" />}
                Verify & Activate
              </button>
            </div>
            {returnTo === "/terminal" ? (
              <p className="sub" style={{ marginTop: 12 }}>
                After verify you can continue to the Terminal, or use the button on the success screen.
              </p>
            ) : null}
          </form>
        </div>
      )}
    </div>
  )
}

/**
 * TotpSetupSection Component.
 * Renders the UI and handles state for the TotpSetupSection feature.
 */
export default TotpSetupSection
