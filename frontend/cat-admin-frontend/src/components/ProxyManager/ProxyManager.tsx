import { useState, useRef, type FC, type FormEvent, useEffect } from "react"
import {
  useProxiesQuery,
  useAddProxyMutation,
  useRemoveProxyMutation,
  useTestProxyMutation,
  useStartProxyOAuthMutation,
  useReindexProxyMutation,
  useUpdateProxyDescriptionMutation,
} from "@/hooks/useProxies"
import { Database, Trash, Refresh, Key } from "@/components/shell/Icons"
import { getErrorMessage } from "@/utils/errors"

/**
 * Manages proxy connections and configurations.
 */
export const ProxyManager: FC = () => {
  const { data: proxies = [], isPending } = useProxiesQuery()
  const addProxyMutation = useAddProxyMutation()
  const removeProxyMutation = useRemoveProxyMutation()
  const testProxyMutation = useTestProxyMutation()
  const startOAuthMutation = useStartProxyOAuthMutation()
  const reindexProxyMutation = useReindexProxyMutation()
  const updateDescMutation = useUpdateProxyDescriptionMutation()

  // Form State
  const [name, setName] = useState("")
  const [transport, setTransport] = useState<"http" | "sse">("http")
  const [url, setUrl] = useState("")
  const [authMode, setAuthMode] = useState<"none" | "bearer" | "oauth">("none")
  const [bearerToken, setBearerToken] = useState("")
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [customDescription, setCustomDescription] = useState("")
  const [workspaceLabel, setWorkspaceLabel] = useState("")
  const [editDesc, setEditDesc] = useState<{ name: string; value: string; workspaceLabel: string } | null>(null)
  const pinged = useRef(false)

  // Automatically test configured proxies once on mount to refresh their live status.
  useEffect(() => {
    if (pinged.current || proxies.length === 0) return
    pinged.current = true
    for (const p of proxies) {
      testProxyMutation.mutate(p.name)
    }
  }, [proxies, testProxyMutation])

  // OAuth Config State
  const [authorizeUrl, setAuthorizeUrl] = useState("")
  const [tokenUrl, setTokenUrl] = useState("")
  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [scopes, setScopes] = useState("")
  const [pkceMethod, setPkceMethod] = useState("S256")
  const [authHeader, setAuthHeader] = useState("Authorization")

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setErrorMsg(null)
    setSuccessMsg(null)

    if (!name.trim() || !url.trim()) {
      setErrorMsg("Name and URL are required.")
      return
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(name)) {
      setErrorMsg("Name must contain only alphanumeric characters, dashes, and underscores.")
      return
    }

    try {
      const parsed = new URL(url.trim())
      if (!["http:", "https:", "ws:", "wss:"].includes(parsed.protocol)) {
        setErrorMsg("URL must start with http://, https://, ws://, or wss://")
        return
      }
    } catch {
      setErrorMsg("Please enter a valid URL (e.g. http://localhost:5001/mcp).")
      return
    }

    let oauthConfig = undefined
    if (authMode === "oauth") {
      oauthConfig = {
        authorize_url: authorizeUrl.trim() || undefined,
        token_url: tokenUrl.trim() || undefined,
        client_id: clientId.trim() || undefined,
        scopes: scopes.trim() ? scopes.trim().split(/\s*,\s*|\s+/) : [],
        pkce: pkceMethod || "S256",
        auth_header: authHeader.trim() || "Authorization",
      }
    }

    addProxyMutation.mutate(
      {
        name: name.trim(),
        transport,
        url: url.trim(),
        authMode,
        bearerToken: authMode === "bearer" ? (bearerToken.trim() || undefined) : undefined,
        oauthConfig: authMode === "oauth" ? oauthConfig : undefined,
        clientSecret: authMode === "oauth" ? (clientSecret.trim() || undefined) : undefined,
        customDescription: customDescription.trim() || undefined,
        workspaceLabel: workspaceLabel.trim() || undefined,
      },
      {
        onSuccess: () => {
          setName("")
          setUrl("")
          setCustomDescription("")
          setWorkspaceLabel("")
          setBearerToken("")
          setAuthorizeUrl("")
          setTokenUrl("")
          setClientId("")
          setClientSecret("")
          setScopes("")
          setAuthHeader("Authorization")
          setPkceMethod("S256")
          setAuthMode("none")
        },
        onError: (err) => {
          setErrorMsg(getErrorMessage(err, "Failed to add upstream proxy."))
        },
      }
    )
  }

  const handleTest = (proxyName: string) => {
    testProxyMutation.mutate(proxyName)
  }

  const handleConnect = (proxyName: string) => {
    startOAuthMutation.mutate(proxyName, {
      onSuccess: (res) => {
        window.location.href = res.authorizeUrl
      },
      onError: (err) => {
        alert(getErrorMessage(err, "Failed to initiate OAuth authorization process."))
      },
    })
  }

  const handleRemove = (proxyName: string) => {
    setSuccessMsg(null)
    setConfirmRemove(proxyName)
  }

  const handleReindex = (proxyName: string) => {
    reindexProxyMutation.mutate(proxyName)
  }


  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Add Proxy Form */}
      <div className="lg:col-span-1 ct-form-section" style={{ marginBottom: 0 }}>
        <h3>Add Upstream MCP</h3>
        <p className="sub">Register new proxy endpoint</p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="ct-form-row one">
            <div className="ct-field">
              <label htmlFor="proxy-name">Namespace / Name</label>
              <input
                id="proxy-name"
                className="ct-input"
                placeholder="e.g. weather-service"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={addProxyMutation.isPending}
              />
              <p style={{ fontSize: 11, color: "var(--fg-subtle)", marginTop: 2 }}>
                Mounted as namespace. Upstream tools become:{" "}
                <code style={{ fontSize: 10.5, padding: "2px 4px", fontFamily: "var(--font-mono)" }}>
                  {name ? `${name}_tool` : "name_tool"}
                </code>
              </p>
            </div>
          </div>

          <div className="ct-form-row">
            <div className="ct-field">
              <label htmlFor="proxy-transport">Transport</label>
              <select
                id="proxy-transport"
                value={transport}
                onChange={(e) => setTransport(e.target.value as "http" | "sse")}
                disabled={addProxyMutation.isPending}
                className="ct-input"
              >
                <option value="http">HTTP</option>
                <option value="sse">SSE</option>
              </select>
            </div>

            <div className="ct-field">
              <label htmlFor="proxy-authmode">Auth Mode</label>
              <select
                id="proxy-authmode"
                value={authMode}
                onChange={(e) => setAuthMode(e.target.value as "none" | "bearer" | "oauth")}
                disabled={addProxyMutation.isPending}
                className="ct-input"
              >
                <option value="none">No Auth</option>
                <option value="bearer">Bearer Token</option>
                <option value="oauth">OAuth PKCE</option>
              </select>
            </div>
          </div>

          <div className="ct-form-row one">
            <div className="ct-field">
              <label htmlFor="proxy-url">Upstream Endpoint URL</label>
              <input
                id="proxy-url"
                className="ct-input"
                placeholder="e.g. http://localhost:5001/mcp"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                disabled={addProxyMutation.isPending}
              />
            </div>
          </div>

          <div className="ct-form-row one">
            <div className="ct-field">
              <label htmlFor="proxy-desc">Custom Tool Description (optional)</label>
              <textarea id="proxy-desc" className="ct-input" rows={3}
                placeholder="Context prepended to every tool from this server (helps the agent choose it)"
                value={customDescription} onChange={(e) => setCustomDescription(e.target.value)}
                disabled={addProxyMutation.isPending} />
            </div>
          </div>

          <div className="ct-form-row one">
            <div className="ct-field">
              <label htmlFor="proxy-workspace-label">Workspace Label (optional)</label>
              <input id="proxy-workspace-label" className="ct-input"
                placeholder="Distinguishes proxies onto the same upstream, e.g. two Notion workspaces"
                value={workspaceLabel} onChange={(e) => setWorkspaceLabel(e.target.value)}
                disabled={addProxyMutation.isPending} />
            </div>
          </div>

          {/* Bearer Token Fields */}
          {authMode === "bearer" && (
            <div className="ct-form-row one animate-in fade-in duration-200">
              <div className="ct-field">
                <label htmlFor="proxy-token">Bearer Auth Token</label>
                <input
                  id="proxy-token"
                  type="password"
                  className="ct-input"
                  placeholder="Write-only bearer token"
                  value={bearerToken}
                  onChange={(e) => setBearerToken(e.target.value)}
                  disabled={addProxyMutation.isPending}
                />
                <p style={{ fontSize: 11, color: "var(--amber)", fontWeight: 500, marginTop: 2 }}>
                  This token is stored encrypted in the secure vault.
                </p>
              </div>
            </div>
          )}

          {/* OAuth Outbound Configuration Fields */}
          {authMode === "oauth" && (
            <div
              className="animate-in fade-in duration-300"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 12,
                padding: 12,
                background: "rgba(251, 191, 36, 0.03)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                marginBottom: 14,
              }}
            >
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10.5,
                  fontWeight: 600,
                  color: "var(--amber)",
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Key className="w-3.5 h-3.5" /> Upstream OAuth Config
              </div>

              <div className="ct-field">
                <label htmlFor="oauth-authorize-url" style={{ fontSize: 10 }}>Authorize URL (Optional - Autodiscovered)</label>
                <input
                  id="oauth-authorize-url"
                  className="ct-input"
                  style={{ padding: "8px 10px", fontSize: 12.5 }}
                  placeholder="e.g. https://id.provider.com/oauth/authorize"
                  value={authorizeUrl}
                  onChange={(e) => setAuthorizeUrl(e.target.value)}
                  disabled={addProxyMutation.isPending}
                />
              </div>

              <div className="ct-field">
                <label htmlFor="oauth-token-url" style={{ fontSize: 10 }}>Token URL (Optional - Autodiscovered)</label>
                <input
                  id="oauth-token-url"
                  className="ct-input"
                  style={{ padding: "8px 10px", fontSize: 12.5 }}
                  placeholder="e.g. https://id.provider.com/oauth/token"
                  value={tokenUrl}
                  onChange={(e) => setTokenUrl(e.target.value)}
                  disabled={addProxyMutation.isPending}
                />
              </div>

              <div className="ct-form-row" style={{ marginBottom: 0 }}>
                <div className="ct-field">
                  <label htmlFor="oauth-client-id" style={{ fontSize: 10 }}>Client ID (Optional)</label>
                  <input
                    id="oauth-client-id"
                    className="ct-input"
                    style={{ padding: "8px 10px", fontSize: 12.5 }}
                    placeholder="Client ID"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    disabled={addProxyMutation.isPending}
                  />
                </div>

                <div className="ct-field">
                  <label htmlFor="oauth-client-secret" style={{ fontSize: 10 }}>Client Secret (Optional)</label>
                  <input
                    id="oauth-client-secret"
                    type="password"
                    className="ct-input"
                    style={{ padding: "8px 10px", fontSize: 12.5 }}
                    placeholder="Write-only client secret"
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                    disabled={addProxyMutation.isPending}
                  />
                </div>
              </div>

              <div className="ct-field">
                <label htmlFor="oauth-scopes" style={{ fontSize: 10 }}>Scopes (comma / space separated)</label>
                <input
                  id="oauth-scopes"
                  className="ct-input"
                  style={{ padding: "8px 10px", fontSize: 12.5 }}
                  placeholder="e.g. read write offline_access"
                  value={scopes}
                  onChange={(e) => setScopes(e.target.value)}
                  disabled={addProxyMutation.isPending}
                />
              </div>

              <div className="ct-form-row" style={{ marginBottom: 0 }}>
                <div className="ct-field">
                  <label htmlFor="oauth-header" style={{ fontSize: 10 }}>Auth Header</label>
                  <input
                    id="oauth-header"
                    className="ct-input"
                    style={{ padding: "8px 10px", fontSize: 12.5 }}
                    placeholder="Authorization"
                    value={authHeader}
                    onChange={(e) => setAuthHeader(e.target.value)}
                    disabled={addProxyMutation.isPending}
                  />
                </div>
                <div className="ct-field">
                  <label htmlFor="oauth-pkce" style={{ fontSize: 10 }}>PKCE Method</label>
                  <select
                    id="oauth-pkce"
                    value={pkceMethod}
                    onChange={(e) => setPkceMethod(e.target.value)}
                    disabled={addProxyMutation.isPending}
                    className="ct-input"
                    style={{ padding: "8px 10px", fontSize: 12.5, height: "auto" }}
                  >
                    <option value="S256">S256 (SHA-256)</option>
                    <option value="none">Plain (No SHA)</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {errorMsg && (
            <div
              style={{
                padding: 10,
                fontSize: 12,
                background: "var(--danger-soft)",
                color: "var(--danger)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid color-mix(in oklch, var(--danger) 35%, var(--border))",
                fontWeight: 500,
                marginBottom: 14,
              }}
            >
              {errorMsg}
            </div>
          )}

          <button
            type="submit"
            className="ct-btn-primary is-block"
            disabled={addProxyMutation.isPending}
          >
            {addProxyMutation.isPending ? "Connecting & Registering..." : "Add & Mount"}
          </button>
        </form>
      </div>

      {/* Proxies List */}
      <div className="lg:col-span-2 ct-panel">
        <div className="ct-panel-head">
          <span className="ct-panel-title">Active Upstream Proxies</span>
          <span className="ct-pill">
            {proxies.length} configured
          </span>
        </div>

        <div className="ct-panel-body" style={{ display: "flex", flexDirection: "column", padding: "0 18px" }}>
          {successMsg && (
            <div
              style={{
                padding: "12px 16px",
                background: "var(--amber-soft)",
                border: "1px solid color-mix(in oklch, var(--amber) 35%, var(--border))",
                borderRadius: "var(--radius-sm)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                marginTop: 16,
                marginBottom: 16,
              }}
            >
              <span style={{ fontSize: 13, color: "var(--amber)", fontWeight: 500 }}>
                {successMsg}
              </span>
              <button
                className="ct-btn-ghost"
                style={{ padding: "4px 10px", fontSize: 12 }}
                onClick={() => setSuccessMsg(null)}
              >
                Dismiss
              </button>
            </div>
          )}

          {confirmRemove && (
            <div
              style={{
                padding: "12px 16px",
                background: "var(--danger-soft)",
                border: "1px solid color-mix(in oklch, var(--danger) 35%, var(--border))",
                borderRadius: "var(--radius-sm)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                marginTop: 16,
                marginBottom: 16,
              }}
            >
              <span style={{ fontSize: 13, color: "var(--danger)", fontWeight: 500 }}>
                Remove proxy <code style={{ fontFamily: "var(--font-mono)" }}>{confirmRemove}</code>?
              </span>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="ct-btn-ghost"
                  style={{ padding: "4px 10px", fontSize: 12 }}
                  onClick={() => setConfirmRemove(null)}
                >
                  Cancel
                </button>
                <button
                  className="ct-btn-danger"
                  style={{ padding: "4px 10px", fontSize: 12 }}
                  disabled={removeProxyMutation.isPending}
                  onClick={() => {
                    const name = confirmRemove
                    setConfirmRemove(null)
                    removeProxyMutation.mutate(name, {
                      onSuccess: (res) => {
                        if (res.restartRequired) {
                          setSuccessMsg("Proxy deleted. However, a server restart is required to fully drop mounted tools from the runtime.")
                        } else {
                          setSuccessMsg("Proxy deleted successfully.")
                        }
                      },
                    })
                  }}
                >
                  Remove
                </button>
              </div>
            </div>
          )}

          {editDesc && (
            <div
              style={{
                padding: "12px 16px",
                background: "var(--amber-soft)",
                border: "1px solid color-mix(in oklch, var(--amber) 35%, var(--border))",
                borderRadius: "var(--radius-sm)",
                display: "flex",
                flexDirection: "column",
                gap: 12,
                marginTop: 16,
                marginBottom: 16,
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label htmlFor="edit-desc-textarea" style={{ fontSize: 13, color: "var(--amber)", fontWeight: 500 }}>
                  Edit description & workspace for <code style={{ fontFamily: "var(--font-mono)" }}>{editDesc.name}</code>:
                </label>
                <textarea
                  id="edit-desc-textarea"
                  className="ct-input"
                  rows={3}
                  value={editDesc.value}
                  onChange={(e) => setEditDesc({ ...editDesc, value: e.target.value })}
                  disabled={updateDescMutation.isPending}
                  placeholder="Context prepended to every tool from this server (helps the agent choose it)"
                />
                <label htmlFor="edit-workspace-input" style={{ fontSize: 11, color: "var(--fg-subtle)", marginTop: 4 }}>
                  Workspace Label:
                </label>
                <input
                  id="edit-workspace-input"
                  className="ct-input"
                  value={editDesc.workspaceLabel}
                  onChange={(e) => setEditDesc({ ...editDesc, workspaceLabel: e.target.value })}
                  disabled={updateDescMutation.isPending}
                  placeholder="e.g. dev, prod, workspace-a"
                />
              </div>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button
                  className="ct-btn-ghost"
                  style={{ padding: "4px 10px", fontSize: 12 }}
                  onClick={() => setEditDesc(null)}
                  disabled={updateDescMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  className="ct-btn-primary"
                  style={{ padding: "4px 10px", fontSize: 12 }}
                  disabled={updateDescMutation.isPending}
                  onClick={() => {
                    updateDescMutation.mutate(
                      {
                        name: editDesc.name,
                        customDescription: editDesc.value.trim() || null,
                        workspaceLabel: editDesc.workspaceLabel.trim() || null,
                      },
                      {
                        onSuccess: () => {
                          setEditDesc(null)
                          setSuccessMsg("Proxy settings updated and routes reindexed.")
                        },
                      }
                    )
                  }}
                >
                  {updateDescMutation.isPending ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          )}

          {isPending ? (
            <div style={{ padding: "24px 0", color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              Loading…
            </div>
          ) : proxies.length === 0 ? (
            <div style={{ textAlign: "center", padding: "48px 18px", color: "var(--fg-subtle)" }}>
              <Database className="w-12 h-12 text-fg-subtle mx-auto mb-3" style={{ opacity: 0.5 }} />
              <div>
                <p className="font-bold text-sm" style={{ color: "var(--fg)" }}>No upstream MCP servers mounted yet.</p>
                <p className="text-xs max-w-sm mx-auto mt-1">
                  Paste an HTTP/SSE endpoint URL on the left to route another MCP server's tools through Whiskers Agent.
                </p>
              </div>
            </div>
          ) : (
            proxies.map((proxy, index) => {
              const isActive = proxy.status === "active"
              const isError = proxy.status === "error"
              const isOauth = proxy.authMode === "oauth"
              const isConnected = proxy.oauthStatus === "connected"

              return (
                <div
                  key={proxy.id}
                  style={{
                    display: "flex",
                    flexDirection: "row",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "16px 0",
                    borderBottom: index === proxies.length - 1 ? "none" : "1px dashed var(--hairline)",
                    gap: 16,
                  }}
                >
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 14.5, fontWeight: 600, color: "var(--amber)" }}>
                        {proxy.name}
                      </span>
                      <span className="ct-pill">{proxy.transport}</span>
                      <span className={`ct-pill ${isActive ? "is-ok" : isError ? "is-err" : ""}`}>
                        {proxy.status}
                      </span>
                      {proxy.authMode === "bearer" && (
                        <span className="ct-pill is-pink">token</span>
                      )}
                      {isOauth && (
                        <span className={`ct-pill ${isConnected ? "is-ok" : "is-warn"}`}>
                          OAuth: {isConnected ? "Connected" : "Requires Login"}
                        </span>
                      )}
                      {proxy.workspaceLabel && (
                        <span className="ct-pill is-purple">workspace: {proxy.workspaceLabel}</span>
                      )}
                    </div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--fg-subtle)", wordBreak: "break-all" }}>
                      {proxy.url}
                    </div>
                    {proxy.customDescription && (
                      <div style={{ fontSize: 12, color: "var(--fg-subtle)", marginTop: 2, fontStyle: "italic", opacity: 0.85 }}>
                        &ldquo;{proxy.customDescription}&rdquo;
                      </div>
                    )}
                    {isActive && (
                      <div style={{ fontSize: 12, color: "var(--neon)", fontWeight: 500, marginTop: 2 }}>
                        {proxy.toolCount} tool{proxy.toolCount === 1 ? "" : "s"} mounted under namespace{" "}
                        <code style={{ fontSize: 11, padding: "1px 4px", background: "rgba(134, 239, 172, 0.1)", color: "var(--neon)", fontFamily: "var(--font-mono)" }}>
                          {proxy.name}_*
                        </code>
                      </div>
                    )}
                    {isError && proxy.errorMessage && (
                      <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 4, paddingLeft: 8, borderLeft: "2px solid var(--danger)" }}>
                        {proxy.errorMessage}
                      </div>
                    )}
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                    {isOauth && !isConnected && (
                      <button
                        className="ct-btn-primary"
                        style={{ padding: "6px 10px", fontSize: 11.5 }}
                        disabled={startOAuthMutation.isPending && startOAuthMutation.variables === proxy.name}
                        onClick={() => handleConnect(proxy.name)}
                      >
                        <Key className="w-3.5 h-3.5" style={{ marginRight: 4 }} />
                        {startOAuthMutation.isPending && startOAuthMutation.variables === proxy.name ? "Authorizing..." : "Connect"}
                      </button>
                    )}
                    {isActive && (
                      <button
                        className="ct-btn-ghost"
                        style={{ padding: "6px 10px", fontSize: 11.5 }}
                        disabled={reindexProxyMutation.isPending && reindexProxyMutation.variables === proxy.name}
                        onClick={() => handleReindex(proxy.name)}
                      >
                        <Refresh
                          className={`w-3.5 h-3.5 ${
                            reindexProxyMutation.isPending && reindexProxyMutation.variables === proxy.name ? "animate-spin text-primary" : ""
                          }`}
                          style={{ marginRight: 4 }}
                        />
                        Reindex
                      </button>
                    )}
                    <button
                      className="ct-btn-ghost"
                      style={{ padding: "6px 10px", fontSize: 11.5 }}
                      onClick={() => setEditDesc({ name: proxy.name, value: proxy.customDescription ?? "", workspaceLabel: proxy.workspaceLabel ?? "" })}
                    >
                      Edit Desc
                    </button>
                    <button
                      className="ct-btn-ghost"
                      style={{ padding: "6px 10px", fontSize: 11.5 }}
                      disabled={testProxyMutation.isPending && testProxyMutation.variables === proxy.name}
                      onClick={() => handleTest(proxy.name)}
                    >
                      <Refresh
                        className={`w-3.5 h-3.5 ${
                          testProxyMutation.isPending && testProxyMutation.variables === proxy.name ? "animate-spin text-primary" : ""
                        }`}
                        style={{ marginRight: 4 }}
                      />
                      Test
                    </button>
                    <button
                      className="ct-btn-danger"
                      style={{ padding: "6px 10px", fontSize: 11.5 }}
                      disabled={removeProxyMutation.isPending && removeProxyMutation.variables === proxy.name}
                      onClick={() => handleRemove(proxy.name)}
                    >
                      <Trash className="w-3.5 h-3.5" style={{ marginRight: 4 }} />
                      Remove
                    </button>
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
