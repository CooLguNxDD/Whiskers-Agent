import { useState, useMemo, type FC } from "react"
import {
  useLlmPoolQuery,
  useAddLlmPoolEntryMutation,
  useDeleteLlmPoolEntryMutation,
  useSetLlmActiveMutation,
  useToggleLlmPoolEntryActiveMutation,
  useCliAgentsQuery,
} from "@/hooks/useConfig"
import { getErrorMessage } from "@/utils/errors"

/** API-based providers that are always available regardless of CLI driver registry. */
const API_PROVIDERS = ["openai", "gemini", "anthropic", "gemini-vertex"] as const

/** CLI driver names that map to a first-class `LLMProvider` (see core/llm_provider.py). */
const CLI_CHAT_DRIVERS = new Set(["claude", "agy", "grok"])

const GraphModelSection: FC = () => {
  const { data, isPending } = useLlmPoolQuery()
  const addMutation = useAddLlmPoolEntryMutation()
  const deleteMutation = useDeleteLlmPoolEntryMutation()
  const setActiveMutation = useSetLlmActiveMutation()
  const toggleActiveMutation = useToggleLlmPoolEntryActiveMutation()

  const { data: cliData } = useCliAgentsQuery()

  // CLI provider ids derived from the live server registry — e.g. "claude-cli", "agy-cli", "grok-cli".
  // Filtered to drivers with a first-class LLMProvider (excludes "generic").
  // Falls back to empty set while loading so the form still renders.
  const CLI_PROVIDERS = useMemo(() => {
    const names = (cliData?.drivers ?? [])
      .filter((d) => CLI_CHAT_DRIVERS.has(d.name))
      .map((d) => `${d.name}-cli`)
    return new Set(names)
  }, [cliData])

  // Full provider list: API providers + available CLI drivers from registry.
  const ALL_PROVIDERS = useMemo(() => {
    const cliProviders = (cliData?.drivers ?? [])
      .filter((d) => CLI_CHAT_DRIVERS.has(d.name))
      .map((d) => ({
        id: `${d.name}-cli`,
        label: `${d.name} (CLI · ${d.available ? "available" : "unavailable"})`,
        available: d.available,
        binary: d.binary,
        isCli: true,
      }))
    const apiProviders = API_PROVIDERS.map((id) => ({
      id,
      label: id,
      available: true,
      binary: "",
      isCli: false,
    }))
    return [...apiProviders, ...cliProviders]
  }, [cliData])

  // New entry form state
  const [name, setName] = useState("")
  const [provider, setProvider] = useState<string>("openai")
  const [model, setModel] = useState("")
  const [kind, setKind] = useState<"chat" | "embedding" | "core">("chat")
  const [dimensions, setDimensions] = useState<number | undefined>(undefined)
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [strength, setStrength] = useState<number>(1.0)

  const [errorMsg, setErrorMsg] = useState("")
  const [successMsg, setSuccessMsg] = useState("")

  if (isPending) {
    return (
      <div className="ct-form-section">
        <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>Loading pool…</div>
      </div>
    )
  }

  const entries = data?.entries || []
  const active = data?.active || {}

  const chats = entries.filter((e) => e.kind === "chat")
  const cores = entries.filter((e) => e.kind === "core")
  const embeddings = entries.filter((e) => e.kind === "embedding")

  // Compute active model lookups once per render instead of re-`find()`ing per JSX usage.
  const activeCoreObj = cores.find((c) => c.id === active.core)
  const activeChatObj = chats.find((c) => c.id === active.chat)
  const activeEmbedObj = embeddings.find((e) => e.id === active.embedding)
  const activeRouteObj = embeddings.find((e) => e.id === active.route)

  async function handleAddEntry(e: React.FormEvent) {
    e.preventDefault()
    setErrorMsg("")
    setSuccessMsg("")

    const isCli = CLI_PROVIDERS.has(provider)
    if (!name.trim() || (!isCli && !model.trim())) {
      setErrorMsg(isCli ? "Name is required." : "Name and Model are required.")
      return
    }
    if (isCli && kind === "embedding") {
      setErrorMsg("CLI providers cannot be used as embedding models. Pick openai/gemini for embeddings.")
      return
    }

    try {
      // CLI providers: leave model empty so the binary uses its default.
      // Never persist provider id as model (e.g. "claude-cli") — Claude Code
      // rejects --model claude-cli with model_not_found / 404.
      // api_key for claude-cli is CLAUDE_CODE_OAUTH_TOKEN (or sk-ant- API key).
      await addMutation.mutateAsync({
        name: name.trim(),
        provider,
        model: model.trim(),
        kind,
        dimensions: kind === "embedding" ? dimensions || 1536 : undefined,
        base_url: baseUrl.trim() || undefined,
        api_key: apiKey.trim() || undefined,
        strength,
      })
      setSuccessMsg(`Successfully added ${name}!`)
      setName("")
      setModel("")
      setBaseUrl("")
      setApiKey("")
      setDimensions(undefined)
      setStrength(1.0)
    } catch (err) {
      setErrorMsg(getErrorMessage(err, "Failed to add pool entry."))
    }
  }

  async function handleDelete(id: string) {
    if (confirm("Are you sure you want to delete this model from the pool?")) {
      try {
        await deleteMutation.mutateAsync(id)
      } catch (err) {
        alert(getErrorMessage(err, "Failed to delete pool entry."))
      }
    }
  }

  async function handleSetActive(kind: "chat" | "embedding" | "route" | "core", id: string) {
    try {
      await setActiveMutation.mutateAsync({ kind, id })
    } catch (err) {
      alert(getErrorMessage(err, "Failed to activate pool entry."))
    }
  }

  async function handleToggleActive(id: string, currentStatus: boolean) {
    try {
      await toggleActiveMutation.mutateAsync({ id, enabled: !currentStatus })
    } catch (err) {
      alert(getErrorMessage(err, "Failed to toggle active status."))
    }
  }

  return (
    <>
      {/* List Active Config */}
      <div className="ct-form-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>Active Graph Models</h3>
          <span className="ct-pill is-ok" style={{ fontFamily: "var(--font-mono)", letterSpacing: "0.06em" }}>
            runtime swappable
          </span>
        </div>
        <p className="sub">
          These models are loaded at runtime by the LangGraph agent and the embedding service without restarting the server.
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Active Core Graph LLM</div>
              <div style={{ fontSize: 12, color: "var(--fg-subtle)", marginTop: 2 }}>
                {activeCoreObj ? (
                  <span>
                    <strong>{activeCoreObj.name}</strong> ({activeCoreObj.provider} · {activeCoreObj.model})
                  </span>
                ) : (
                  <span style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>Default (Environment Variables)</span>
                )}
              </div>
            </div>
            <span className="ct-pill" style={{ height: "fit-content" }}>{active.core ? "Database Active" : "Env Fallback"}</span>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Active Execution LLM (Chat)</div>
              <div style={{ fontSize: 12, color: "var(--fg-subtle)", marginTop: 2 }}>
                {activeChatObj ? (
                  <span>
                    <strong>{activeChatObj.name}</strong> ({activeChatObj.provider} · {activeChatObj.model})
                  </span>
                ) : (
                  <span style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>Default (Environment Variables)</span>
                )}
              </div>
            </div>
            <span className="ct-pill" style={{ height: "fit-content" }}>{active.chat ? "Database Active" : "Env Fallback"}</span>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Active Data Embedding Model</div>
              <div style={{ fontSize: 12, color: "var(--fg-subtle)", marginTop: 2 }}>
                {activeEmbedObj ? (
                  <span>
                    <strong>{activeEmbedObj.name}</strong> ({activeEmbedObj.provider} · {activeEmbedObj.model})
                  </span>
                ) : (
                  <span style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>Default (Environment Variables)</span>
                )}
              </div>
            </div>
            <span className="ct-pill" style={{ height: "fit-content" }}>{active.embedding ? "Database Active" : "Env Fallback"}</span>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Active Route Embedding Model (Discovery)</div>
              <div style={{ fontSize: 12, color: "var(--fg-subtle)", marginTop: 2 }}>
                {activeRouteObj ? (
                  <span>
                    <strong>{activeRouteObj.name}</strong> ({activeRouteObj.provider} · {activeRouteObj.model})
                  </span>
                ) : (
                  <span style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>Default (Environment Variables)</span>
                )}
              </div>
            </div>
            <span className="ct-pill" style={{ height: "fit-content" }}>{active.route ? "Database Active" : "Env Fallback"}</span>
          </div>
        </div>
      </div>

      {/* Model Pool Table */}
      <div className="ct-form-section">
        <h3>Model Config Pool</h3>
        <p className="sub">Manage registered models and keys. Active entries override server env defaults.</p>

        {entries.length === 0 ? (
          <div style={{ textAlign: "center", padding: "20px", color: "var(--fg-subtle)", border: "1px dashed var(--border)", borderRadius: 6 }}>
            No models in pool. Add a model below to configure dynamic routing.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 14 }}>
            {entries.map((entry) => {
              const isChatActive = entry.kind === "chat" && active.chat === entry.id
              const isCoreActive = entry.kind === "core" && active.core === entry.id
              const isEmbeddingActive = entry.kind === "embedding" && active.embedding === entry.id
              const isRouteActive = entry.kind === "embedding" && active.route === entry.id
              const isActiveStyle = isChatActive || isCoreActive || isEmbeddingActive || isRouteActive
              const isRowMuted = entry.is_active === false
              return (
                <div
                  key={entry.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "12px 14px",
                    border: isActiveStyle ? "1px solid var(--neon)" : "1px solid var(--border)",
                    borderRadius: 8,
                    background: "var(--bg-card)",
                    gap: 12,
                    opacity: isRowMuted ? 0.45 : 1.0,
                  }}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <strong style={{ fontSize: 14 }}>{entry.name}</strong>
                      <span className="ct-pill" style={{ textTransform: "uppercase", fontSize: 10 }}>{entry.kind}</span>
                      {entry.strength !== undefined && (
                        <span className="ct-pill" style={{ fontSize: 10, background: "rgba(255,255,255,0.07)", border: "1px solid var(--border)", color: "var(--fg-subtle)" }}>
                          Strength: {entry.strength}
                        </span>
                      )}
                      {entry.has_api_key && (
                        <span className="ct-pill is-ok" style={{ fontSize: 10 }}>Key Locked</span>
                      )}
                      {!entry.is_active && (
                        <span className="ct-pill" style={{ fontSize: 10, background: "rgba(255,255,255,0.03)", color: "var(--fg-subtle)" }}>Inactive</span>
                      )}
                    </div>
                    <div style={{ fontSize: 12, color: "var(--fg-subtle)", marginTop: 4 }}>
                      Provider: <code style={{ color: "var(--fg)" }}>{entry.provider}</code> · Model: <code style={{ color: "var(--fg)" }}>{entry.model}</code>
                      {entry.dimensions && ` · Dims: ${entry.dimensions}`}
                      {entry.base_url && ` · Base URL: ${entry.base_url}`}
                      {entry.strength !== undefined && ` · Strength: ${entry.strength}`}
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <button
                      className="ct-btn-ghost"
                      style={{ padding: "4px 8px", fontSize: 12, height: 28, color: entry.is_active ? "var(--fg-subtle)" : "var(--neon)" }}
                      onClick={() => handleToggleActive(entry.id, !!entry.is_active)}
                    >
                      {entry.is_active ? "Disable" : "Enable"}
                    </button>

                    {entry.is_active && (
                      <>
                        {entry.kind === "chat" && (
                          !isChatActive ? (
                            <button
                              className="ct-btn-ghost"
                              style={{ padding: "4px 8px", fontSize: 12, height: 28 }}
                              onClick={() => handleSetActive("chat", entry.id)}
                            >
                              Activate
                            </button>
                          ) : (
                            <span className="ct-pill is-ok" style={{ height: 22, display: "flex", alignItems: "center" }}>Active</span>
                          )
                        )}
                        {entry.kind === "core" && (
                          !isCoreActive ? (
                            <button
                              className="ct-btn-ghost"
                              style={{ padding: "4px 8px", fontSize: 12, height: 28 }}
                              onClick={() => handleSetActive("core", entry.id)}
                            >
                              Activate (Core)
                            </button>
                          ) : (
                            <span className="ct-pill is-ok" style={{ height: 22, display: "flex", alignItems: "center" }}>Active (Core)</span>
                          )
                        )}
                        {entry.kind === "embedding" && (
                          <>
                            {!isEmbeddingActive ? (
                              <button
                                className="ct-btn-ghost"
                                style={{ padding: "4px 8px", fontSize: 12, height: 28 }}
                                onClick={() => handleSetActive("embedding", entry.id)}
                              >
                                Activate (Data)
                              </button>
                            ) : (
                              <span className="ct-pill is-ok" style={{ height: 22, display: "flex", alignItems: "center" }}>Active (Data)</span>
                            )}
                            {!isRouteActive ? (
                              <button
                                className="ct-btn-ghost"
                                style={{ padding: "4px 8px", fontSize: 12, height: 28 }}
                                onClick={() => handleSetActive("route", entry.id)}
                              >
                                Activate (Route)
                              </button>
                            ) : (
                              <span className="ct-pill is-ok" style={{ height: 22, display: "flex", alignItems: "center" }}>Active (Route)</span>
                            )}
                          </>
                        )}
                      </>
                    )}
                    <button
                      className="ct-btn-ghost"
                      style={{ padding: "4px 8px", fontSize: 12, height: 28, color: "var(--danger)" }}
                      onClick={() => handleDelete(entry.id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Add New Model Form */}
      <div className="ct-form-section">
        <h3>Add Model to Pool</h3>
        <p className="sub">API keys are securely encrypted using pgcrypto and never returned in subsequent API reads.</p>

        <form onSubmit={handleAddEntry} style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 14 }}>
          {errorMsg && <div style={{ color: "var(--danger)", fontSize: 12, fontWeight: 600 }}>{errorMsg}</div>}
          {successMsg && <div style={{ color: "var(--neon)", fontSize: 12, fontWeight: 600 }}>{successMsg}</div>}

          <div className="ct-form-row">
            <div className="ct-field">
              <label htmlFor="config-name">Configuration Name</label>
              <input
                id="config-name"
                className="ct-input"
                placeholder="e.g. My Fast OpenAI"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <div className="ct-field">
              <label htmlFor="model-kind">Model Type / Kind</label>
              <select
                id="model-kind"
                className="ct-input"
                value={kind}
                onChange={(e) => setKind(e.target.value as "chat" | "embedding" | "core")}
                style={{ background: "var(--bg-input)" }}
              >
                <option value="chat">Execution Chat LLM</option>
                <option value="core">Core Graph LLM</option>
                <option value="embedding">Embedding Model</option>
              </select>
            </div>
          </div>

          <div className="ct-form-row">
            <div className="ct-field">
              <label htmlFor="model-provider">Provider</label>
              <select
                id="model-provider"
                className="ct-input"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                style={{ background: "var(--bg-input)" }}
              >
                {ALL_PROVIDERS.map(p => (
                  <option key={p.id} value={p.id} disabled={p.isCli && !p.available}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="ct-field">
              <label htmlFor="model-name">Model Name</label>
              <input
                id="model-name"
                className="ct-input"
                placeholder={
                  CLI_PROVIDERS.has(provider)
                    ? "optional CLI model flag (e.g. sonnet)"
                    : "e.g. gpt-4o or text-embedding-3-small"
                }
                value={model}
                onChange={(e) => setModel(e.target.value)}
                required={!CLI_PROVIDERS.has(provider)}
              />
            </div>
          </div>

          <div className="ct-form-row">
            <div className="ct-field">
              <label htmlFor="model-api-key">
                {CLI_PROVIDERS.has(provider)
                  ? provider === "agy-cli"
                    ? "API Key override (optional, encrypted)"
                    : "OAuth token / API key override (optional, encrypted)"
                  : "API Key (Encrypted write-only, optional)"}
              </label>
              <input
                id="model-api-key"
                className="ct-input"
                type="password"
                placeholder={
                  CLI_PROVIDERS.has(provider)
                    ? provider === "agy-cli"
                      ? "leave empty — uses server env for agy"
                      : "claude setup-token value, or sk-ant-… API key"
                    : provider === "gemini-vertex"
                      ? "Vertex AI Express API Key"
                      : "••••••••••••••••"
                }
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                aria-describedby={
                  provider === "gemini-vertex"
                    ? "model-api-key-help-vertex"
                    : CLI_PROVIDERS.has(provider)
                      ? "model-api-key-help-cli"
                      : undefined
                }
              />
              {provider === "gemini-vertex" && (
                <span
                  id="model-api-key-help-vertex"
                  style={{ fontSize: 11, color: "var(--fg-subtle)", marginTop: 4, display: "block" }}
                >
                  Requires a Vertex AI Express API Key (plain string, not a GCP JSON service account key).
                </span>
              )}
              {CLI_PROVIDERS.has(provider) && (
                <span
                  id="model-api-key-help-cli"
                  style={{ fontSize: 11, color: "var(--fg-subtle)", marginTop: 4, display: "block" }}
                >
                  Runs headless <code>{provider.replace("-cli", "")}</code> on the server PATH
                  (see CLAUDE_CLI_BINARY / AGY_CLI_BINARY).
                  {provider === "claude-cli" ? (
                    <>
                      {" "}Paste a <code>claude setup-token</code> value to override{" "}
                      <code>CLAUDE_CODE_OAUTH_TOKEN</code>, or an <code>sk-ant-…</code> key for{" "}
                      <code>ANTHROPIC_API_KEY</code>. Leave empty to use container env.
                    </>
                  ) : (
                    <> Keep a real EMBED_PROVIDER for embeddings.</>
                  )}
                </span>
              )}
            </div>
            {kind === "embedding" ? (
              <div className="ct-field">
                <label htmlFor="model-dimensions">Dimensions (Embedding only)</label>
                <input
                  id="model-dimensions"
                  className="ct-input"
                  type="number"
                  placeholder="1536"
                  value={dimensions || ""}
                  onChange={(e) => setDimensions(parseInt(e.target.value) || undefined)}
                />
              </div>
            ) : (
              <div className="ct-field">
                <label htmlFor="model-base-url">Base URL Override (Optional)</label>
                <input
                  id="model-base-url"
                  className="ct-input"
                  placeholder="e.g. http://localhost:1234/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
            )}
          </div>

          {kind === "embedding" && (
            <div className="ct-form-row">
              <div className="ct-field">
                <label htmlFor="model-base-url-embed">Base URL Override (Optional)</label>
                <input
                  id="model-base-url-embed"
                  className="ct-input"
                  placeholder="e.g. http://localhost:1234/v1"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
            </div>
          )}

          <div className="ct-form-row">
            <div className="ct-field">
              <label htmlFor="model-strength">Model Strength / Weight (Default: 1.0)</label>
              <input
                id="model-strength"
                className="ct-input"
                type="number"
                step="any"
                placeholder="e.g. 0.3 for Gemini Lite, 100 for Claude Opus"
                value={strength}
                onChange={(e) => setStrength(parseFloat(e.target.value) || 0)}
              />
              <span style={{ fontSize: 11, color: "var(--fg-subtle)", marginTop: 4, display: "block" }}>
                Auto-select selects the model with the highest strength weight when no active override is set.
              </span>
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
            <button className="ct-btn-primary" type="submit" disabled={addMutation.isPending}>
              {addMutation.isPending ? "Adding…" : "Add to pool"}
            </button>
          </div>
        </form>
      </div>
    </>
  )
}

/**
 * GraphModelSection Component.
 * Renders the UI and handles state for the GraphModelSection feature.
 */
export default GraphModelSection
