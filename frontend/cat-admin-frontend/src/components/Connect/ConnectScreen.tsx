/**
 * ConnectScreen Component
 *
 * ct-themed full-page Layer-2 connect screen: OAuth vs Direct Login method
 * tabs, a plugin<->provider diagram, a success banner once authorized, and a
 * typed-confirm revoke modal to disconnect. Wired to the real Layer-2 relay
 * and Vault-backed direct-credentials endpoints.
 */
import { useState, type FC, type FormEvent } from "react"
import { Link } from "@tanstack/react-router"
import { ArrowLeft, Bolt, Check, Lock } from "@/components/shell/Icons"
import { useSetDirectCredentialsMutation, useClearDirectCredentialsMutation } from "@/hooks/useDirectCredentials"
import { useRevokeOAuthMutation } from "@/hooks/usePlugins"
import { getProviderMeta } from "./providers"
import MethodTabs, { type ConnectMethod } from "./MethodTabs"
import SuccessBanner from "./SuccessBanner"
import ConnectRevokeModal from "./ConnectRevokeModal"
import { getErrorMessage } from "@/utils/errors"
import { bus } from "@/events/bus"

export interface ConnectScreenProps {
  pluginId: string
  provider: string
  layer2OauthEnabled: boolean
  oauthConnected: boolean
  directConnected: boolean
  hasReturnState: boolean
  state: string
  onOauthRevoked: () => void
}

/**
 * Title-cases a plugin id for display, stripping a trailing "plugin" word (e.g. "whiskers_report_plugin" -> "Whiskers Agent Report").
 */
function titleCasePlugin(pluginId: string): string {
  const display = pluginId.replace(/_/g, " ").replace(/\bplugin\b/, "").trim()
  return display
    .split(" ")
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ")
}

/**
 * ConnectScreen Component.
 * Renders the ct-themed Layer-2 connect UI and wires it to the OAuth relay
 * authorize redirect and the direct-credentials vault mutations.
 */
const ConnectScreen: FC<ConnectScreenProps> = ({
  pluginId,
  provider,
  layer2OauthEnabled,
  oauthConnected,
  directConnected,
  hasReturnState,
  state,
  onOauthRevoked,
}) => {
  const [method, setMethod] = useState<ConnectMethod>(layer2OauthEnabled ? "oauth" : "direct")
  const [basicMethod, setBasicMethod] = useState<"password" | "token">("password")
  const [username, setUsername] = useState("")
  const [secret, setSecret] = useState("")
  const [showSecret, setShowSecret] = useState(false)
  const [revokeOpen, setRevokeOpen] = useState(false)
  const [bannerDismissed, setBannerDismissed] = useState(false)

  const setMutation = useSetDirectCredentialsMutation(pluginId)
  const clearMutation = useClearDirectCredentialsMutation(pluginId)
  const revokeOauthMutation = useRevokeOAuthMutation(pluginId)

  const prov = getProviderMeta(provider)
  const isOauth = method === "oauth"
  const directLinkedNow = directConnected || setMutation.data?.status === "ok"
  const connected = isOauth ? oauthConnected : directLinkedNow

  const titleCase = titleCasePlugin(pluginId)

  // Guard against malformed/unexpected provider values before building the redirect URL.
  const safeProvider = provider && /^[a-zA-Z0-9_-]+$/.test(provider) ? provider : "whiskers_core"
  const authorizeUrl =
    `/oauth/plugin/${encodeURIComponent(safeProvider)}/authorize?plugin_id=${encodeURIComponent(pluginId)}` +
    (state ? `&state=${encodeURIComponent(state)}` : "")
  const finishHref = `/oauth/complete-layer1/${encodeURIComponent(state)}`

  function handleSubmitDirect(e: FormEvent) {
    e.preventDefault()
    if (basicMethod === "password") {
      if (!username || !secret) return
      setMutation.mutate(
        { username, password: secret },
        { onSuccess: (data) => { if (data.status === "ok") { setUsername(""); setSecret("") } } },
      )
    } else {
      if (!secret) return
      setMutation.mutate({ api_token: secret }, { onSuccess: (data) => { if (data.status === "ok") setSecret("") } })
    }
  }

  async function handleRevokeConfirm() {
    try {
      if (isOauth) {
        await revokeOauthMutation.mutateAsync(safeProvider)
        onOauthRevoked()
      } else {
        await clearMutation.mutateAsync()
        setUsername("")
        setSecret("")
      }
    } catch (err) {
      console.error("Failed to revoke authorization:", err)
      bus.emit("toast:error", { message: getErrorMessage(err, "Failed to revoke authorization") })
    } finally {
      setRevokeOpen(false)
    }
  }

  return (
    <>
      {connected && !bannerDismissed && (
        <SuccessBanner
          isOauth={isOauth}
          providerMeta={prov}
          pluginId={pluginId}
          hasReturnState={hasReturnState}
          finishHref={finishHref}
          onDismiss={() => setBannerDismissed(true)}
        />
      )}

      {/* corner crumbs */}
      <Link
        to="/"
        search={{ oauth_success: undefined, state: undefined }}
        style={{
          position: "absolute",
          top: connected && !bannerDismissed ? 86 : 26,
          left: 28,
          transition: "top 240ms cubic-bezier(.2,.7,.2,1)",
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          letterSpacing: "0.14em",
          textTransform: "uppercase",
          color: "var(--fg-muted)",
          cursor: "pointer",
          padding: "6px 10px",
          borderRadius: 999,
          border: "1px solid var(--hairline)",
          background: "var(--bg-sunken)",
          textDecoration: "none",
        }}
      >
        <ArrowLeft width="12" height="12" />
        back to console
      </Link>
      <div
        style={{
          position: "absolute",
          top: connected && !bannerDismissed ? 86 : 26,
          right: 28,
          transition: "top 240ms cubic-bezier(.2,.7,.2,1)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          letterSpacing: "0.14em",
          textTransform: "uppercase",
          color: "var(--fg-subtle)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span>{isOauth ? "oauth handshake" : "direct credentials"}</span>
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: connected ? "var(--neon)" : "var(--amber)",
            boxShadow: `0 0 8px ${connected ? "var(--neon)" : "var(--amber)"}`,
          }}
        />
        <span style={{ color: connected ? "var(--neon)" : "var(--amber)" }}>
          {connected ? "complete" : isOauth ? "awaiting consent" : "awaiting credentials"}
        </span>
      </div>

      <div className="ct-login-card" style={{ width: 460, gap: 20 }}>
      {/* plugin <-> provider diagram */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 22, padding: "8px 0 0" }}>
        <div
          style={{
            width: 64,
            height: 64,
            borderRadius: 16,
            background: "linear-gradient(135deg, color-mix(in oklch, var(--amber) 25%, var(--bg-sunken)), var(--bg-sunken))",
            border: "1px solid color-mix(in oklch, var(--amber) 30%, var(--border))",
            color: "var(--amber)",
            display: "grid",
            placeItems: "center",
            boxShadow: "var(--glow-amber)",
          }}
        >
          <Bolt width="30" height="30" />
        </div>

        <div style={{ position: "relative", flex: "0 0 78px", height: 28, display: "flex", alignItems: "center" }}>
          <div style={{ position: "absolute", inset: "50% 0 auto 0", height: 1, background: "var(--border)", transform: "translateY(-50%)" }} />
          <div
            style={{
              position: "absolute",
              inset: "50% 0 auto 0",
              height: 1,
              background: `linear-gradient(90deg, transparent, ${connected ? "var(--neon)" : "var(--amber)"}, transparent)`,
              boxShadow: `0 0 8px ${connected ? "var(--neon)" : "var(--amber)"}`,
              transform: "translateY(-50%)",
            }}
          />
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: "50%",
              transform: "translate(-50%, -50%)",
              fontFamily: "var(--font-mono)",
              fontSize: 9.5,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: connected ? "var(--neon)" : "var(--amber)",
              padding: "2px 8px",
              background: "var(--card)",
              border: `1px solid ${connected ? "color-mix(in oklch, var(--neon) 35%, var(--border))" : "color-mix(in oklch, var(--amber) 35%, var(--border))"}`,
              borderRadius: 999,
            }}
          >
            {connected ? "linked" : isOauth ? "oauth" : "direct"}
          </div>
        </div>

        <div
          style={{
            width: 64,
            height: 64,
            borderRadius: 16,
            background: "var(--bg-sunken)",
            border: `1px solid color-mix(in oklch, ${prov.color} 35%, var(--border))`,
            color: prov.color,
            display: "grid",
            placeItems: "center",
            fontFamily: "var(--font-mono)",
            fontSize: 18,
            fontWeight: 700,
            letterSpacing: "0.02em",
            boxShadow: `0 0 0 1px color-mix(in oklch, ${prov.color} 28%, transparent), 0 0 22px -6px color-mix(in oklch, ${prov.color} 50%, transparent)`,
          }}
        >
          {prov.name.slice(0, 2).toUpperCase()}
        </div>
      </div>

      <div style={{ textAlign: "center", display: "flex", flexDirection: "column", gap: 6 }}>
        <div className="ct-eyebrow">connect plugin</div>
        <div className="ct-login-title" style={{ fontSize: 22, letterSpacing: "-0.02em" }}>
          {titleCase} ↔ {prov.name}
        </div>
        <div className="ct-login-sub" style={{ fontFamily: "var(--font-sans, inherit)", fontSize: 13, color: "var(--fg-muted)", letterSpacing: 0 }}>
          {isOauth ? (
            <>
              Authorize the MCP server to act on your behalf in{" "}
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>{prov.handle}</span>.
            </>
          ) : (
            <>
              Store {basicMethod === "token" ? "an API token" : "a username & password"} in the credential vault to
              authorize <span style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>{prov.handle}</span>.
            </>
          )}
        </div>
      </div>

      <MethodTabs
        method={method}
        onChange={setMethod}
        oauthOn={oauthConnected}
        directOn={directLinkedNow}
        oauthEnabled={layer2OauthEnabled}
      />

      {isOauth ? (
        <div style={{ border: "1px solid var(--hairline)", borderRadius: 10, background: "var(--bg-sunken)", padding: "12px 14px" }}>
          <div className="ct-eyebrow" style={{ marginBottom: 8 }}>
            what the plugin will be allowed to do
          </div>
          {prov.scopes.length > 0 ? (
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
              {prov.scopes.map((s) => (
                <li key={s} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
                  <Check width="12" height="12" style={{ color: "var(--neon)" }} />
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>{s}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div style={{ fontSize: 12.5, color: "var(--fg-muted)" }}>Delegated access as declared in the plugin manifest.</div>
          )}
          <div
            style={{
              fontSize: 11.5,
              color: "var(--fg-muted)",
              marginTop: 10,
              paddingTop: 10,
              borderTop: "1px dashed var(--hairline)",
              lineHeight: 1.55,
            }}
          >
            {prov.desc}
          </div>
        </div>
      ) : (
        <div style={{ border: "1px solid var(--hairline)", borderRadius: 10, background: "var(--bg-sunken)", padding: "14px 14px 12px", display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
            <div className="ct-eyebrow">sign in to {prov.name.toLowerCase()}</div>
            <div
              role="tablist"
              aria-label="credential type"
              style={{
                display: "inline-flex",
                background: "var(--bg)",
                border: "1px solid var(--hairline)",
                borderRadius: 999,
                padding: 2,
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                letterSpacing: "0.14em",
                textTransform: "uppercase",
              }}
            >
              {(["password", "token"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="tab"
                  aria-selected={basicMethod === m}
                  aria-controls="login-tabpanel"
                  onClick={() => setBasicMethod(m)}
                  style={{
                    padding: "4px 10px",
                    borderRadius: 999,
                    border: "none",
                    cursor: "pointer",
                    background: basicMethod === m ? "var(--amber)" : "transparent",
                    color: basicMethod === m ? "var(--bg)" : "var(--fg-muted)",
                    fontWeight: basicMethod === m ? 700 : 500,
                    letterSpacing: "inherit",
                    textTransform: "inherit",
                    fontFamily: "inherit",
                    fontSize: "inherit",
                  }}
                >
                  {m === "password" ? "password" : "api token"}
                </button>
              ))}
            </div>
          </div>

          <form
            id="login-tabpanel"
            role="tabpanel"
            onSubmit={handleSubmitDirect}
            style={{ display: "flex", flexDirection: "column", gap: 12 }}
          >
            {basicMethod === "password" ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div className="ct-field">
                  <label htmlFor="cn-user">Username · email</label>
                  <input
                    id="cn-user"
                    className="ct-input"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    autoComplete="username"
                    placeholder={`you@${prov.handle}`}
                    disabled={setMutation.isPending || clearMutation.isPending}
                  />
                </div>
                <div className="ct-field">
                  <label htmlFor="cn-pw">
                    Password
                    <button
                      type="button"
                      className="hint"
                      style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
                      onClick={() => setShowSecret((v) => !v)}
                    >
                      {showSecret ? "hide" : "reveal"}
                    </button>
                  </label>
                  <input
                    id="cn-pw"
                    className="ct-input"
                    type={showSecret ? "text" : "password"}
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    autoComplete="current-password"
                    disabled={setMutation.isPending || clearMutation.isPending}
                  />
                </div>
              </div>
            ) : (
              <div className="ct-field">
                <label htmlFor="cn-tok">
                  API token
                  <button
                    type="button"
                    className="hint"
                    style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
                    onClick={() => setShowSecret((v) => !v)}
                  >
                    {showSecret ? "hide" : "reveal"}
                  </button>
                </label>
                <input
                  id="cn-tok"
                  className="ct-input"
                  type={showSecret ? "text" : "password"}
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  placeholder="paste from provider dashboard"
                  style={{ fontFamily: "var(--font-mono)" }}
                  disabled={setMutation.isPending || clearMutation.isPending}
                />
              </div>
            )}

            {setMutation.data?.status === "auth_failed" && (
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  padding: "9px 12px",
                  borderRadius: 8,
                  background: "color-mix(in oklch, var(--danger) 12%, transparent)",
                  border: "1px solid color-mix(in oklch, var(--danger) 35%, var(--border))",
                  color: "var(--danger)",
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                }}
              >
                Authentication failed. Verify your credentials and check the plugin configuration.
              </div>
            )}
            {setMutation.isError && (
              <div style={{ fontSize: 12, color: "var(--danger)", fontFamily: "var(--font-mono)" }}>
                {getErrorMessage(setMutation.error, "Save failed. Please try again.")}
              </div>
            )}

            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 8,
                fontSize: 11,
                color: "var(--fg-muted)",
                lineHeight: 1.5,
                paddingTop: 4,
                borderTop: "1px dashed var(--hairline)",
              }}
            >
              <Lock width="11" height="11" style={{ marginTop: 3, color: "var(--amber)", flex: "0 0 auto" }} />
              <span>
                Stored encrypted in the credential vault (AES-256-GCM). Replayed only on requests originating from{" "}
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>{pluginId}</span>.
              </span>
            </div>

            {!connected && (
              <button
                type="submit"
                className="ct-btn-primary is-block"
                disabled={
                  setMutation.isPending ||
                  clearMutation.isPending ||
                  (basicMethod === "password" ? !username || !secret : !secret)
                }
                style={{ gap: 8 }}
              >
                <Lock width="13" height="13" />
                {setMutation.isPending ? "Signing in…" : `Sign in to ${prov.name}`}
              </button>
            )}
          </form>
        </div>
      )}

      {connected && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 12px",
            background: "color-mix(in oklch, var(--neon) 10%, transparent)",
            border: "1px solid color-mix(in oklch, var(--neon) 35%, var(--border))",
            borderRadius: 10,
            fontSize: 12.5,
            color: "var(--neon)",
            fontFamily: "var(--font-mono)",
          }}
        >
          <Check width="14" height="14" />
          <span>
            connected · <span style={{ color: "var(--fg)" }}>{prov.name}</span> {isOauth ? "token" : "credentials"} now
            held by {pluginId}
          </span>
        </div>
      )}

      {/* primary CTA — OAuth only (Direct's submit lives inside its form above) */}
      {isOauth && !connected && (
        <a href={authorizeUrl} className="ct-btn-primary is-block" style={{ gap: 8 }}>
          <Lock width="13" height="13" />
          Connect to {prov.name}
        </a>
      )}
      {connected && (
        <button
          type="button"
          className="ct-btn-danger-solid is-block"
          onClick={() => setRevokeOpen(true)}
          style={{ gap: 8 }}
        >
          <Lock width="13" height="13" />
          Disconnect · revoke {isOauth ? "OAuth token" : "credentials"}
        </button>
      )}

      {hasReturnState && (
        <a
          href={finishHref}
          className="ct-btn-ghost is-block"
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: 6,
            borderColor: connected ? "color-mix(in oklch, var(--amber) 50%, var(--border))" : "var(--hairline)",
            color: connected ? "var(--amber)" : "var(--fg-subtle)",
            opacity: connected ? 1 : 0.55,
            pointerEvents: connected ? "auto" : "none",
            fontWeight: 600,
          }}
          aria-disabled={!connected}
          tabIndex={connected ? 0 : -1}
          title={connected ? "Complete MCP authorization" : `Connect with ${prov.name} first`}
        >
          Finish · return to MCP client →
        </a>
      )}

      <div className="ct-login-meta">
        <span className="ct-server-pill">
          <span className="dot" />
          mcp.whiskers.local
        </span>
        <span className="ct-pill" style={{ fontSize: 10 }}>
          Layer 2 · {isOauth ? "OAuth" : basicMethod === "token" ? "API token" : "Username · password"}
        </span>
      </div>
      </div>

      <div
        style={{
          position: "absolute",
          left: 24,
          bottom: 18,
          fontSize: 11,
          color: "var(--fg-subtle)",
          fontFamily: "var(--font-mono)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          letterSpacing: "0.10em",
          textTransform: "uppercase",
        }}
      >
        <span className="ct-status-dot" style={{ width: 6, height: 6 }} />
        plugin = {pluginId} · provider = {provider}
      </div>
      <div
        style={{
          position: "absolute",
          right: 24,
          bottom: 18,
          fontSize: 11,
          color: "var(--fg-subtle)",
          display: "flex",
          gap: 16,
          fontFamily: "var(--font-mono)",
          letterSpacing: "0.10em",
          textTransform: "uppercase",
        }}
      >
        <a href="#" style={{ color: "inherit" }}>scopes</a>
        <a href="#" style={{ color: "inherit" }}>privacy</a>
        <a href="#" style={{ color: "inherit" }}>revoke later</a>
      </div>

      <ConnectRevokeModal
        open={revokeOpen}
        isOauth={isOauth}
        basicMethod={basicMethod}
        providerMeta={prov}
        pluginId={pluginId}
        onCancel={() => setRevokeOpen(false)}
        onConfirm={handleRevokeConfirm}
      />
    </>
  )
}

export default ConnectScreen
