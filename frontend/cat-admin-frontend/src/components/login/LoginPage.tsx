import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import BrandMark from "@/components/shell/BrandMark"
import { useShallow } from "zustand/react/shallow"
import { usePreferencesStore, selectThemeAttrs, useSessionStore } from "@/store"
import { getAdminExists } from "@/api/admin"
import { Route } from "@/routes/login"

/**
 * Redirects the user to a new URL by setting window.location.href.
 */
function redirectUser(url: string) {
  window.location.href = url
}

/**
 * LoginPage component.
 */
export function LoginPage() {
  const { next, state: urlState } = Route.useSearch()
  const navigate = useNavigate()
  const { mcpState, setMcpState } = useSessionStore()

  // Capture OAuth return state once on mount (no ongoing URL→store re-sync).
  useEffect(() => {
    if (urlState) setMcpState(urlState)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const state = mcpState || urlState || ""

  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [useApiKey, setUseApiKey] = useState(false)
  const [apiKey, setApiKey] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [checking, setChecking] = useState(true)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const themeAttrs = usePreferencesStore(useShallow(selectThemeAttrs))

  useEffect(() => () => { if (timeoutRef.current !== null) clearTimeout(timeoutRef.current) }, [])

  useEffect(() => {
    let mounted = true
    getAdminExists()
      .then((exists) => {
        if (mounted && exists === false) {
          void navigate({ to: "/signup", search: { state }, replace: true })
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const url = useApiKey ? "/api/admin/public/login-api-key" : "/api/admin/public/login"
      const body = useApiKey
        ? { api_key: apiKey, state, next }
        : { username, password, state, next }

      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        const data = (await res.json()) as { redirect: string }
        redirectUser(data.redirect)
        return
      }
      if (res.status === 429) {
        const data = (await res.json()) as { retry_after?: number }
        const seconds = data.retry_after ?? 60
        setBlocked(true)
        setError(`Too many attempts. Try again in ${seconds}s.`)
        if (timeoutRef.current !== null) clearTimeout(timeoutRef.current)
        timeoutRef.current = setTimeout(() => setBlocked(false), seconds * 1000)
      } else if (res.status === 401) {
        setError("Invalid credentials.")
      } else {
        setError("Login failed. Please try again.")
      }
    } catch {
      setError("Network error. Please try again.")
    } finally {
      setLoading(false)
    }
  }

  if (checking) {
    return (
      <div className="ct-root ct-login" {...themeAttrs}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--fg-muted)", letterSpacing: "0.12em" }}>
          loading…
        </span>
      </div>
    )
  }

  return (
    <div className="ct-root ct-login" {...themeAttrs}>
      {/* top-left corner label */}
      <div style={{
        position: "absolute", left: 28, top: 26,
        fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.16em",
        textTransform: "uppercase", color: "var(--fg-subtle)",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <span style={{ color: "var(--amber)" }}>◆</span> whiskers.agent · admin
      </div>

      <form onSubmit={handleSubmit}>
        <div className="ct-login-card">
          <div className="ct-login-brand">
            <BrandMark size={56} />
            <div style={{ textAlign: "center", display: "flex", flexDirection: "column", gap: 4 }}>
              <div className="ct-login-title">welcome back, kitten</div>
              <div className="ct-login-sub">whiskers.agent · mcp admin</div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {!useApiKey ? (
              <>
                <div className="ct-field">
                  <label htmlFor="username">Username</label>
                  <input
                    id="username"
                    className="ct-input"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    disabled={loading || blocked}
                    autoFocus
                    autoComplete="username"
                    placeholder="admin"
                  />
                </div>
                <div className="ct-field">
                  <label htmlFor="password">
                    Password
                    <button
                      type="button"
                      className="hint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                      style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      aria-pressed={showPassword}
                    >
                      {showPassword ? "hide" : "show"}
                    </button>
                  </label>
                  <input
                    id="password"
                    className="ct-input"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={loading || blocked}
                    autoComplete="current-password"
                    placeholder="••••••••••••"
                  />
                </div>
              </>
            ) : (
              <div className="ct-field">
                <label htmlFor="apiKey">API Key</label>
                <input
                  id="apiKey"
                  className="ct-input"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  disabled={loading || blocked}
                  autoFocus
                  placeholder="octk_..."
                />
              </div>
            )}
          </div>

          {error && (
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "9px 12px",
              borderRadius: 8,
              background: "color-mix(in oklch, var(--danger) 12%, transparent)",
              border: "1px solid color-mix(in oklch, var(--danger) 35%, var(--border))",
              color: "var(--danger)",
              fontSize: 12.5,
              fontFamily: "var(--font-mono)",
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            className="ct-btn-primary is-block"
            disabled={loading || blocked || (useApiKey ? !apiKey : (!username || !password))}
            style={{ opacity: loading || blocked || (useApiKey ? !apiKey : (!username || !password)) ? 0.55 : 1 }}
          >
            {loading ? "entering the console…" : "enter the console →"}
          </button>

          <div className="ct-login-meta">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span className="ct-server-pill">
                <span className="dot" />
                mcp.whiskers.local
              </span>
              <button
                type="button"
                className="ct-login-link focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: 12 }}
                onClick={() => {
                  setError(null)
                  setUseApiKey((v) => !v)
                }}
                aria-label={useApiKey ? "Use password" : "Use API key"}
                aria-pressed={useApiKey}
              >
                {useApiKey ? "Use password" : "Use API key"}
              </button>
            </div>
            <Link
              to="/signup"
              search={{ state }}
              className="ct-login-link"
              style={{ fontSize: 12 }}
            >
              first run? set up →
            </Link>
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
        all paws operational
      </div>

      {/* bottom-right links */}
      <div style={{
        position: "absolute", right: 24, bottom: 18,
        fontSize: 11, color: "var(--fg-subtle)",
        display: "flex", gap: 16,
        fontFamily: "var(--font-mono)", letterSpacing: "0.10em", textTransform: "uppercase",
      }}>
        <a href="https://github.com/CooLguNxDD/OpenCat-Mcp-Full" style={{ color: "inherit" }}>docs</a>
      </div>
    </div>
  )
}
