import { useEffect, useState } from "react"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import BrandMark from "@/components/shell/BrandMark"
import { useShallow } from "zustand/react/shallow"
import { usePreferencesStore, selectThemeAttrs, useSessionStore } from "@/store"
import { getAdminExists } from "@/api/admin"

function passwordStrength(pw: string): number {
  let s = 0
  if (pw.length >= 8) s++
  if (pw.length >= 14) s++
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++
  if (/[0-9]/.test(pw) && /[^A-Za-z0-9]/.test(pw)) s++
  return Math.min(s, 4)
}

const STRENGTH_LABELS = ["—", "weak", "ok", "strong", "purrfect"]

/**
 * Signup route configuration.
 */
export const Route = createFileRoute("/signup")({
  validateSearch: (s: Record<string, unknown>) => ({
    state: typeof s.state === "string" && s.state.length <= 500 ? s.state : "",
  }),
  component: function SignupPage() {
    const { state: urlState } = Route.useSearch()
    const navigate = useNavigate()
    const { mcpState, setMcpState } = useSessionStore()

    useEffect(() => {
      if (urlState) {
        setMcpState(urlState)
      }
    }, [urlState, setMcpState])

    const state = mcpState || urlState || ""
    const [username, setUsername] = useState("")
    const [password, setPassword] = useState("")
    const [confirmPassword, setConfirmPassword] = useState("")
    const [agreed, setAgreed] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [loading, setLoading] = useState(false)
    const [checking, setChecking] = useState(true)

    const themeAttrs = usePreferencesStore(useShallow(selectThemeAttrs))

    useEffect(() => {
      let mounted = true
      getAdminExists()
        .then((exists) => {
          if (mounted && exists === true) {
            void navigate({ to: "/login", search: { next: "", state }, replace: true })
          }
        })
        .catch((err) => {
          console.error("Failed to check admin existence", err)
        })
        .finally(() => {
          if (mounted) setChecking(false)
        })
      return () => {
        mounted = false
      }
    }, [navigate, state])

    const strength = passwordStrength(password)
    const passwordMismatch = confirmPassword.length > 0 && password !== confirmPassword
    const canSubmit = agreed && strength >= 3 && username.length > 0 && password === confirmPassword && !loading

    if (checking) {
      return (
        <div className="ct-root ct-login" {...themeAttrs}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--fg-muted)", letterSpacing: "0.12em" }}>
            loading…
          </span>
        </div>
      )
    }

    async function handleSubmit(e: React.FormEvent) {
      e.preventDefault()
      if (!canSubmit) return
      setError(null)
      setLoading(true)
      try {
        const res = await fetch("/api/admin/public/signup", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password, state }),
        })
        if (res.ok) {
          const data = (await res.json()) as { redirect: string }
          window.location.href = data.redirect
          return
        }
        if (res.status === 409) {
          setError("Admin account already exists.")
          void navigate({ to: "/login", search: { next: "", state } })
          return
        }
        const data = (await res.json()) as { error?: string }
        setError(data.error ?? "Signup failed. Please try again.")
      } catch {
        setError("Network error. Please try again.")
      } finally {
        setLoading(false)
      }
    }

    return (
      <div className="ct-root ct-login" {...themeAttrs}>
        {/* top-left: first-run label */}
        <div style={{
          position: "absolute", left: 28, top: 26,
          fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.16em",
          textTransform: "uppercase", color: "var(--fg-subtle)",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ color: "var(--amber)" }}>◆</span> whiskers.agent / first-run
        </div>

        {/* top-right: step indicator */}
        <div style={{
          position: "absolute", right: 28, top: 26,
          fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.14em",
          textTransform: "uppercase", color: "var(--fg-subtle)",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ color: "var(--amber)" }}>step 1 · admin</span>
          <span style={{ opacity: 0.35 }}>—</span>
          <span>step 2 · node</span>
          <span style={{ opacity: 0.35 }}>—</span>
          <span>step 3 · plugins</span>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="ct-login-card" style={{ width: 420 }}>
            <div className="ct-login-brand">
              <BrandMark size={56} />
              <div style={{ textAlign: "center", display: "flex", flexDirection: "column", gap: 4 }}>
                <div className="ct-login-title">claim this node, kitten</div>
                <div className="ct-login-sub">create the first super-admin</div>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="ct-field">
                <label htmlFor="su-username">Username</label>
                <input
                  id="su-username"
                  className="ct-input"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={loading}
                  autoFocus
                  autoComplete="username"
                  placeholder="admin"
                />
              </div>

              <div className="ct-field" style={{ marginBottom: -2 }}>
                <label htmlFor="su-password">
                  Password
                  {password.length > 0 && (
                    <span
                      className="hint"
                      style={{ color: strength >= 3 ? "var(--neon)" : strength === 2 ? "var(--amber)" : "var(--danger)" }}
                    >
                      {STRENGTH_LABELS[strength]}
                    </span>
                  )}
                </label>
                <input
                  id="su-password"
                  className="ct-input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                  autoComplete="new-password"
                  placeholder="choose a strong password"
                />
                {/* strength bar */}
                <div className={"ct-strength is-" + strength} style={{ marginTop: 8 }}>
                  <span className="seg" /><span className="seg" />
                  <span className="seg" /><span className="seg" />
                </div>
                {password.length > 0 && strength < 3 && (
                  <div className="ct-strength-label">
                    {strength === 0 && "too short"}
                    {strength === 1 && "add uppercase + number + symbol"}
                    {strength === 2 && "add a number and special character"}
                  </div>
                )}
              </div>

              <div className="ct-field">
                <label htmlFor="su-confirm">
                  Confirm password
                  {passwordMismatch && (
                    <span className="hint" style={{ color: "var(--danger)" }}>mismatch</span>
                  )}
                </label>
                <input
                  id="su-confirm"
                  className="ct-input"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  disabled={loading}
                  autoComplete="new-password"
                  placeholder="repeat password"
                  style={passwordMismatch ? { borderColor: "color-mix(in oklch, var(--danger) 60%, var(--border))" } : undefined}
                />
              </div>
            </div>

            {/* agreed toggle */}
            <label style={{
              display: "flex", alignItems: "flex-start", gap: 10,
              fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.5,
              cursor: "pointer",
            }}>
              <button
                type="button"
                className={"ct-switch" + (agreed ? " is-on" : "")}
                role="switch"
                aria-checked={agreed}
                style={{ flexShrink: 0, marginTop: 1 }}
                onClick={() => setAgreed((v) => !v)}
                aria-label="I agree"
              />
              <span>
                I'll handle plugin credentials responsibly. Tokens shown once;{" "}
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>whiskers.agent</span>{" "}
                never phones home unless I let it.
              </span>
            </label>

            {error && (
              <div style={{
                padding: "9px 12px", borderRadius: 8,
                background: "color-mix(in oklch, var(--danger) 12%, transparent)",
                border: "1px solid color-mix(in oklch, var(--danger) 35%, var(--border))",
                color: "var(--danger)", fontSize: 12.5, fontFamily: "var(--font-mono)",
              }}>
                {error}
              </div>
            )}

            <button
              type="submit"
              className="ct-btn-primary is-block"
              disabled={!canSubmit}
              style={{ opacity: canSubmit ? 1 : 0.45 }}
            >
              {loading ? "claiming the console…" : "claim the console →"}
            </button>

            <div className="ct-login-meta">
              <span style={{ fontSize: 12, color: "var(--fg-muted)" }}>
                already wandering?{" "}
                <Link to="/login" search={{ next: "", state }} className="ct-login-link">
                  sign in
                </Link>
              </span>
              <span className="ct-server-pill">
                <span className="dot" />
                mcp.whiskers.local
              </span>
            </div>
          </div>
        </form>

        {/* bottom-left status */}
        <div style={{
          position: "absolute", left: 24, bottom: 18,
          fontSize: 11, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)",
          display: "flex", alignItems: "center", gap: 8,
          letterSpacing: "0.10em", textTransform: "uppercase",
        }}>
          <span className="ct-status-dot" style={{ width: 6, height: 6 }} />
          no admin found · ready to imprint
        </div>

        {/* bottom-right links */}
        <div style={{
          position: "absolute", right: 24, bottom: 18,
          fontSize: 11, color: "var(--fg-subtle)", display: "flex", gap: 16,
          fontFamily: "var(--font-mono)", letterSpacing: "0.10em", textTransform: "uppercase",
        }}>
          <a href="#" style={{ color: "inherit" }}>docs</a>
          <a href="#" style={{ color: "inherit" }}>recovery</a>
          <a href="#" style={{ color: "inherit" }}>cli setup</a>
        </div>
      </div>
    )
  },
})
