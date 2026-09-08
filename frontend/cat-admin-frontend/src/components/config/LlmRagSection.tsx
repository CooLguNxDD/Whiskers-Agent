/**
 * LlmRagSection
 *
 * Config page section for LLM provider, model overrides, local LLM endpoint,
 * and RAG / embedding settings.  Reads current values from /api/config/llm
 * and saves non-sensitive overrides back via POST.  API keys are never shown.
 */

import { useState, useEffect, type FC } from "react"
import { useLlmConfigQuery, useSaveLlmConfigMutation } from "@/hooks/useConfig"
import type { LlmConfigUpdate } from "@/api/config"

const PROVIDERS = [
  { id: "openai",    label: "OpenAI",    model: "gpt-4o" },
  { id: "anthropic", label: "Anthropic", model: "claude-sonnet-4-6" },
  { id: "gemini",    label: "Gemini",    model: "gemini-2.5-flash" },
] as const



/** Returns a fresh draft object from LlmConfig query data. */
function buildDraft(data: LlmConfigUpdate | undefined): LlmConfigUpdate {
  return {
    llm_provider:      data?.llm_provider      ?? "openai",
    llm_model:         data?.llm_model          ?? "",
    embed_provider:    data?.embed_provider      ?? "",
    embed_model:       data?.embed_model         ?? "",
    embed_base_url:    data?.embed_base_url      ?? "",
    embed_dimensions:  data?.embed_dimensions    ?? 1536,
    rag_enabled:       data?.rag_enabled         ?? true,
    local_llm_enabled: data?.local_llm_enabled   ?? false,
    local_base_url:    data?.local_base_url       ?? "",
    cli_agent_provider: data?.cli_agent_provider ?? "",
  }
}

/** LLM provider + RAG settings section for the system config page. */
const LlmRagSection: FC = () => {
  const { data, isPending } = useLlmConfigQuery()
  const { mutate: save, isPending: isSaving, isSuccess } = useSaveLlmConfigMutation()

  const [draft, setDraft] = useState<LlmConfigUpdate>(buildDraft(undefined))

  // Initialise draft once data arrives
  useEffect(() => {
    if (data) {
      const timer = setTimeout(() => {
        setDraft(buildDraft(data))
      }, 0)
      return () => clearTimeout(timer)
    }
  }, [data])

  function set<K extends keyof LlmConfigUpdate>(key: K, value: LlmConfigUpdate[K]) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  const providerMeta = PROVIDERS.find((p) => p.id === draft.llm_provider)

  if (isPending) {
    return (
      <div className="ct-form-section">
        <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>Loading…</div>
      </div>
    )
  }

  return (
    <>
      {/* Provider picker */}
      <div className="ct-form-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>LLM Provider</h3>
          <span className="ct-pill" style={{ fontFamily: "var(--font-mono)", letterSpacing: "0.06em" }}>
            overrides server .env
          </span>
        </div>
        <p className="sub">
          Used by the <code style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>run_graph</code> tool
          and any plugin that calls the gateway LLM.
        </p>

        <div className="ct-llm-providers">
          {PROVIDERS.map((p) => (
            <button
              key={p.id}
              className={"ct-llm-provider" + (draft.llm_provider === p.id ? " is-active" : "")}
              onClick={() => set("llm_provider", p.id)}
              type="button"
            >
              <span className="ct-llm-provider-dot" />
              <span className="ct-llm-provider-name">{p.label}</span>
              <span className="ct-llm-provider-model">{p.model}</span>
            </button>
          ))}
        </div>

        {/* API key indicator — read-only, never shown */}
        <div className="ct-form-row one" style={{ marginTop: 14 }}>
          <div className="ct-field">
            <label>
              API Key
              <span className="hint" style={{ color: "var(--fg-subtle)", fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
                &nbsp;(env var — not stored here)
              </span>
            </label>
            <input
              className="ct-input"
              readOnly
              value={data?.has_api_key ? "••••••••••••••••" : "not set"}
              style={{ color: data?.has_api_key ? "var(--neon)" : "var(--danger)", cursor: "default" }}
            />
          </div>
        </div>

        <div className="ct-form-row">
          <div className="ct-field">
            <label>
              LLM_MODEL
              <span style={{ color: "var(--fg-subtle)", fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>&nbsp;(override)</span>
            </label>
            <input
              className="ct-input"
              placeholder={providerMeta?.model ?? "provider default"}
              value={draft.llm_model}
              onChange={(e) => set("llm_model", e.target.value)}
            />
          </div>
          <div className="ct-field">
            <label>Default model</label>
            <input
              className="ct-input"
              value={providerMeta?.model ?? ""}
              readOnly
              style={{ opacity: 0.55 }}
            />
          </div>
        </div>
      </div>

      {/* Local LLM */}
      <div className="ct-form-section">
        <div className="ct-checkbox-row" style={{ paddingTop: 0 }}>
          <button
            className={"ct-switch" + (draft.local_llm_enabled ? " is-on" : "")}
            role="switch"
            aria-checked={draft.local_llm_enabled}
            onClick={() => set("local_llm_enabled", !draft.local_llm_enabled)}
            aria-label="local-llm"
          />
          <div style={{ flex: 1 }}>
            <div className="label">Use local LLM endpoint</div>
            <div className="desc">Route text generation through an OpenAI-compatible endpoint (LM Studio, Ollama, vLLM).</div>
          </div>
        </div>
        <div className="ct-llm-sub" data-disabled={!draft.local_llm_enabled}>
          <div className="ct-form-row">
            <div className="ct-field">
              <label>LLM_BASE_URL</label>
              <input
                className="ct-input"
                placeholder="http://127.0.0.1:1234/v1"
                value={draft.local_base_url}
                onChange={(e) => set("local_base_url", e.target.value)}
                disabled={!draft.local_llm_enabled}
              />
            </div>
          </div>
        </div>
      </div>

      {/* RAG / Embedding */}
      <div className="ct-form-section">
        <div className="ct-checkbox-row" style={{ paddingTop: 0, alignItems: "center" }}>
          <button
            className={"ct-switch" + (draft.rag_enabled ? " is-on" : "")}
            role="switch"
            aria-checked={draft.rag_enabled}
            onClick={() => set("rag_enabled", !draft.rag_enabled)}
            aria-label="rag"
          />
          <div style={{ flex: 1 }}>
            <div className="label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              Enable RAG
              <span className="ct-pill is-amber" style={{ fontFamily: "var(--font-mono)" }}>
                LangGraph · Embedding
              </span>
            </div>
            <div className="desc">
              Activate the semantic route search graph and embedding pipeline. Plugins gain a{" "}
              <code style={{ fontFamily: "var(--font-mono)", color: "var(--fg)" }}>retrieve</code> step before tool calls.
            </div>
          </div>
        </div>

        <div className="ct-llm-sub" data-disabled={!draft.rag_enabled}>
          <div className="ct-form-row">
            <div className="ct-field">
              <label>EMBED_PROVIDER</label>
              <input
                className="ct-input"
                placeholder="same as LLM provider"
                value={draft.embed_provider}
                onChange={(e) => set("embed_provider", e.target.value)}
                disabled={!draft.rag_enabled}
              />
            </div>
            <div className="ct-field">
              <label>EMBED_MODEL</label>
              <input
                className="ct-input"
                placeholder="text-embedding-3-small"
                value={draft.embed_model}
                onChange={(e) => set("embed_model", e.target.value)}
                disabled={!draft.rag_enabled}
              />
            </div>
          </div>
          <div className="ct-form-row">
            <div className="ct-field">
              <label>EMBED_BASE_URL</label>
              <input
                className="ct-input"
                placeholder="http://host.docker.internal:1234/v1"
                value={draft.embed_base_url}
                onChange={(e) => set("embed_base_url", e.target.value)}
                disabled={!draft.rag_enabled}
              />
            </div>
            <div className="ct-field">
              <label>EMBED_DIMENSIONS</label>
              <input
                className="ct-input"
                placeholder="1536"
                value={draft.embed_dimensions}
                onChange={(e) => set("embed_dimensions", parseInt(e.target.value) || 1536)}
                disabled={!draft.rag_enabled}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Save / Reset */}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginBottom: 16 }}>
        {isSuccess && (
          <span className="ct-pill is-ok" style={{ fontFamily: "var(--font-mono)", marginRight: "auto" }}>
            Saved
          </span>
        )}
        <button
          className="ct-btn-ghost"
          onClick={() => data && setDraft(buildDraft(data))}
          disabled={isSaving}
        >
          Revert
        </button>
        <button
          className="ct-btn-primary"
          onClick={() => save(draft)}
          disabled={isSaving}
        >
          {isSaving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </>
  )
}

/**
 * LlmRagSection Component.
 * Renders the UI and handles state for the LlmRagSection feature.
 */
export default LlmRagSection
