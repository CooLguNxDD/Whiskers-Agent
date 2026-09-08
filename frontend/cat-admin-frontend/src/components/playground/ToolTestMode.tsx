/**
 * ToolTestMode — individual tool testing mode for the Playground.
 *
 * Renders a three-panel layout: tool selection rail (left), schema + payload
 * editor (center), and response stats + raw output (right). Auto-generates
 * a dummy payload from the tool's JSON Schema input definition and fires the
 * real tool via POST /api/playground/tools/invoke.
 */

import { getErrorMessage } from "@/utils/errors"
import { useState, useMemo, useRef, useEffect } from "react"
import { Search, Refresh, Play, Copy, Check, Download, Chevron } from "@/components/shell/Icons"
import { invokePlaygroundTool } from "@/api/playground"
import type { InvokeResult } from "@/api/playground"
import { useToolRegistry } from "./useToolRegistry"
import { dummyFromSchema, schemaTypeLabel } from "./utils"
import { useShallow } from "zustand/react/shallow"
import { useUIStore } from "@/store"
import type { JSONSchemaProperty, PGTool } from "./types"
import { SchemaForm } from "@/components/inference/SchemaForm"
import type { AsyncOption, AsyncOptionsSpec } from "@/components/inference/SchemaForm"
import { useCatalogClient } from "@/hooks/useCatalog"
import { resolveCatalogOp } from "./resolveCatalogOp"

// ─── Schema Tree ───────────────────────────────────────────────────────────────

/** Recursive schema property tree — renders typed fields with required markers. */
function SchemaTree({
  schema,
  depth = 0,
}: {
  schema: JSONSchemaProperty
  depth?: number
}) {
  const props = schema.properties ?? {}
  const required = new Set(schema.required ?? [])
  const entries = Object.entries(props)

  if (entries.length === 0) {
    return (
      <ul className="pg-schema" style={{ paddingLeft: 0 }}>
        <li
          className="pg-schema-row"
          style={{
            color: "var(--fg-subtle)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
          }}
        >
          no parameters
        </li>
      </ul>
    )
  }

  return (
    <ul className="pg-schema" style={{ paddingLeft: depth ? 14 : 0 }}>
      {entries.map(([k, v]) => (
        <li key={k} className="pg-schema-row">
          <span className="pg-schema-key">
            {k}
            {required.has(k) && <span className="pg-schema-req">*</span>}
          </span>
          <span className="pg-schema-type">{schemaTypeLabel(v)}</span>
          {v.description && (
            <span className="pg-schema-desc">{v.description}</span>
          )}
          {v.type === "object" && v.properties && (
            <SchemaTree schema={v} depth={depth + 1} />
          )}
        </li>
      ))}
    </ul>
  )
}

// ─── Tool Test Mode ────────────────────────────────────────────────────────────

/**
 * Individual tool testing mode: select a tool, inspect its schema, edit the
 * auto-generated payload, and fire the real tool call against the server.
 */
export function ToolTestMode() {
  const {
    playgroundToolRegistryCollapsed,
    togglePlaygroundToolRegistryCollapsed,
    playgroundResponseCollapsed,
    togglePlaygroundResponseCollapsed,
  } = useUIStore(
    useShallow((s) => ({
      playgroundToolRegistryCollapsed: s.playgroundToolRegistryCollapsed,
      togglePlaygroundToolRegistryCollapsed: s.togglePlaygroundToolRegistryCollapsed,
      playgroundResponseCollapsed: s.playgroundResponseCollapsed,
      togglePlaygroundResponseCollapsed: s.togglePlaygroundResponseCollapsed,
    }))
  )
  const { tools, loading: toolsLoading } = useToolRegistry()
  const { client: catalogClient } = useCatalogClient()
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  // Payload edits are keyed to a tool; switching tools falls back to a fresh dummy.
  const [draft, setDraft] = useState<{ tool: string; text: string } | null>(null)
  const [lastResult, setLastResult] = useState<InvokeResult | null>(null)
  const [firing, setFiring] = useState(false)
  const [copied, setCopied] = useState(false)
  const [descExpanded, setDescExpanded] = useState(false)
  const [prevSelectedName, setPrevSelectedName] = useState<string | null>(null)

  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const selected: PGTool | null = useMemo(
    () => tools.find((t) => t.name === selectedName) ?? tools[0] ?? null,
    [tools, selectedName]
  )

  // Reset description expanded state when tool changes
  if (selected?.name !== prevSelectedName) {
    setPrevSelectedName(selected?.name ?? null)
    setDescExpanded(false)
  }

  const generated = useMemo(
    () => (selected ? JSON.stringify(dummyFromSchema(selected.input), null, 2) : ""),
    [selected]
  )
  const payload = draft && draft.tool === selected?.name ? draft.text : generated
  // Only show a result that belongs to the currently selected tool.
  const result = lastResult && lastResult.tool === selected?.name ? lastResult : null

  const filtered = useMemo(
    () => (query
      ? tools.filter(
          (t) =>
            t.name.toLowerCase().includes(query.toLowerCase()) ||
            t.plugin.toLowerCase().includes(query.toLowerCase())
        )
      : tools),
    [tools, query]
  )

  const grouped = useMemo(() => {
    const map = new Map<string, PGTool[]>()
    for (const t of filtered) {
      const list = map.get(t.plugin) ?? []
      list.push(t)
      map.set(t.plugin, list)
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [filtered])

  const requiredCount = (selected?.input.required ?? []).length
  const catalogOp = selected ? resolveCatalogOp(catalogClient, selected) : null
  const formSchema = (catalogOp?.input_schema as Record<string, unknown> | undefined) ?? selected?.input

  const loadAsyncOptions = async (spec: AsyncOptionsSpec): Promise<AsyncOption[]> => {
    const result = await catalogClient.op(spec.plugin_id, spec.operation_id)(spec.args ?? {})
    const items = Array.isArray(result)
      ? result
      : result && typeof result === "object" && Array.isArray((result as { results?: unknown }).results)
        ? (result as { results: unknown[] }).results
        : []
    return items.map((item) => {
      if (item && typeof item === "object") {
        const rec = item as Record<string, unknown>
        const value = rec[spec.valueKey]
        const label = rec[spec.labelKey] ?? value
        return { value: String(value ?? ""), label: String(label ?? "") }
      }
      return { value: String(item), label: String(item) }
    })
  }

  const fire = async (args?: Record<string, unknown>) => {
    if (!selected || firing) return
    let parsed = args
    if (!parsed) {
      try {
        const raw = JSON.parse(payload)
        if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
          setLastResult({
            ok: false,
            tool: selected.name,
            ms: 0,
            structured_content: null,
            content: [],
            error: "payload must be a JSON object",
            error_type: "ClientValidation",
          })
          return
        }
        parsed = raw as Record<string, unknown>
      } catch {
        setLastResult({
          ok: false,
          tool: selected.name,
          ms: 0,
          structured_content: null,
          content: [],
          error: "payload is not valid JSON",
          error_type: "ClientValidation",
        })
        return
      }
    }
    setDraft({ tool: selected.name, text: JSON.stringify(parsed, null, 2) })
    setFiring(true)
    const started = performance.now()
    try {
      const op = resolveCatalogOp(catalogClient, selected)
      const result = op
        ? await catalogClient.call(op.plugin_id, op.operation_id, parsed)
        : await invokePlaygroundTool(selected.name, parsed)
      const ms = Math.round(performance.now() - started)
      if (op) {
        setLastResult({
          ok: true,
          tool: selected.name,
          ms,
          structured_content: result,
          content: [],
          error: null,
          error_type: null,
        })
      } else {
        setLastResult({ ...(result as InvokeResult), ms })
      }
    } catch (e) {
      setLastResult({
        ok: false,
        tool: selected.name,
        ms: Math.round(performance.now() - started),
        structured_content: null,
        content: [],
        error: getErrorMessage(e),
        error_type: "RequestFailed",
      })
    } finally {
      setFiring(false)
    }
  }

  const responseBody = result
    ? JSON.stringify(result.structured_content ?? result.content, null, 2)
    : null
  const [copyError, setCopyError] = useState<string | null>(null)

  const copyResponse = () => {
    if (!responseBody) return
    setCopyError(null)
    navigator.clipboard
      .writeText(responseBody)
      .then(() => {
        setCopied(true)
        if (timeoutRef.current) clearTimeout(timeoutRef.current)
        timeoutRef.current = setTimeout(() => setCopied(false), 1500)
      })
      .catch((err) => {
        console.error("Failed to copy response to clipboard", err)
        setCopyError("Failed to copy to clipboard")
        if (timeoutRef.current) clearTimeout(timeoutRef.current)
        timeoutRef.current = setTimeout(() => setCopyError(null), 3000)
      })
  }

  if (!selected) {
    return (
      <div
        style={{
          padding: "60px 0",
          textAlign: "center",
          color: "var(--fg-subtle)",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        {toolsLoading ? "loading tool registry…" : "no tools exposed by the server"}
      </div>
    )
  }

  return (
    <div
      className={
        "pg-test" +
        (playgroundToolRegistryCollapsed ? " rail-collapsed" : "") +
        (playgroundResponseCollapsed ? " right-collapsed" : "")
      }
    >
      {/* Left rail: tool selector */}
      <aside className={"pg-rail" + (playgroundToolRegistryCollapsed ? " is-collapsed" : "")}>
        <div className="pg-rail-head">
          <span className="ct-eyebrow">tool registry</span>
          <span className="ct-pill" style={{ padding: "2px 8px", fontSize: 10 }} title="tools currently exposed">
            {tools.length} available
          </span>
          <button
            className="pg-rail-toggle"
            onClick={togglePlaygroundToolRegistryCollapsed}
            title={playgroundToolRegistryCollapsed ? "Show tool registry" : "Hide tool registry"}
          >
            <Chevron width="13" height="13" style={{ transform: playgroundToolRegistryCollapsed ? "none" : "rotate(180deg)" }} />
          </button>
        </div>
        <div className="pg-rail-search">
          <Search width="13" height="13" />
          <input
            placeholder="filter…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="pg-rail-list">
          {grouped.map(([plugin, pluginTools]) => (
            <div key={plugin}>
              <div className="ct-eyebrow" style={{ padding: "8px 10px 4px" }}>{plugin}</div>
              {pluginTools.map((t) => (
                <button
                  key={t.name}
                  className={"pg-rail-tool" + (selected.name === t.name ? " is-active" : "")}
                  onClick={() => setSelectedName(t.name)}
                >
                  <div className="pg-rail-name">{t.name}</div>
                  <div className="pg-rail-plugin">{t.plugin}</div>
                </button>
              ))}
            </div>
          ))}
        </div>
      </aside>

      {/* Center: schema + payload */}
      <section className="pg-test-mid">
        <div className="pg-test-head">
          <div>
            <div className="pg-test-name">{selected.name}</div>
            <div className="pg-test-desc">
              {selected.desc && (
                <>
                  {selected.desc.length > 100 && !descExpanded ? (
                    <>
                      {selected.desc.slice(0, 100)}...{" "}
                      <button
                        onClick={() => setDescExpanded(true)}
                        style={{
                          background: "none",
                          border: "none",
                          color: "var(--amber)",
                          cursor: "pointer",
                          padding: 0,
                          fontFamily: "inherit",
                          fontSize: "inherit",
                          fontWeight: "500",
                          textDecoration: "underline",
                        }}
                      >
                        show more
                      </button>
                    </>
                  ) : (
                    <>
                      {selected.desc}
                      {selected.desc.length > 100 && (
                        <>
                          {" "}
                          <button
                            onClick={() => setDescExpanded(false)}
                            style={{
                              background: "none",
                              border: "none",
                              color: "var(--amber)",
                              cursor: "pointer",
                              padding: 0,
                              fontFamily: "inherit",
                              fontSize: "inherit",
                              fontWeight: "500",
                              textDecoration: "underline",
                            }}
                          >
                            show less
                          </button>
                        </>
                      )}
                    </>
                  )}
                </>
              )}
            </div>
          </div>
          <div className="pg-test-actions">
            <button
              className="ct-btn-ghost"
              style={{ fontSize: 11.5 }}
              onClick={() => {
                setDraft(null)
                setLastResult(null)
              }}
            >
              <Refresh width="13" height="13" /> Regenerate
            </button>
            <button
              className="ct-btn-primary"
              disabled={firing}
              onClick={() => {
                void fire()
              }}
            >
              <Play width="12" height="12" /> {firing ? "Firing…" : "Fire call"}
            </button>
          </div>
        </div>

        {/* Input schema tree */}
        <div className="ct-panel" style={{ marginBottom: 14 }}>
          <div className="ct-panel-head">
            <span className="ct-panel-title">input schema</span>
            <span className="ct-pill">
              <span className="mono">required ×{requiredCount}</span>
            </span>
          </div>
          <div className="ct-panel-body">
            <SchemaTree schema={selected.input} />
          </div>
        </div>

        {/* Schema-driven form + JSON fallback */}
        <div className="ct-panel">
          <div className="ct-panel-head">
            <span className="ct-panel-title">arguments</span>
            <span className="ct-eyebrow" style={{ color: "var(--neon)" }}>
              schema form
            </span>
          </div>
          <div className="ct-panel-body">
            <SchemaForm
              key={catalogOp ? `${catalogOp.plugin_id}:${catalogOp.operation_id}` : selected.name}
              schema={formSchema as Record<string, unknown>}
              initialValues={dummyFromSchema(selected.input) as Record<string, unknown>}
              submitLabel={firing ? "Firing…" : "Fire call"}
              disabled={firing}
              loadAsyncOptions={loadAsyncOptions}
              onSubmit={(values) => fire(values)}
            />
          </div>
          <details style={{ padding: "0 12px 12px" }}>
            <summary className="ct-eyebrow" style={{ cursor: "pointer" }}>raw JSON</summary>
            <textarea
              className="pg-code-input"
              value={payload}
              onChange={(e) => setDraft({ tool: selected.name, text: e.target.value })}
              rows={8}
              spellCheck={false}
            />
          </details>
        </div>
      </section>

      {/* Right panel: response */}
      <aside className={"pg-test-right" + (playgroundResponseCollapsed ? " is-collapsed" : "")}>
        <div className="pg-test-resphead">
          <span className={"ct-pill " + (result ? (result.ok ? "is-ok" : "is-err") : "")}>
            <span className={"ct-dot " + (result ? (result.ok ? "is-ok" : "is-err") : "is-off")} />
            {result ? (result.ok ? "ok" : "error") : firing ? "firing…" : "not fired"}
          </span>
          {result && <span className="pg-tok">{result.ms}ms</span>}
          <button
            className="pg-rail-toggle"
            onClick={togglePlaygroundResponseCollapsed}
            title={playgroundResponseCollapsed ? "Show response" : "Hide response"}
          >
            <Chevron width="13" height="13" style={{ transform: playgroundResponseCollapsed ? "rotate(180deg)" : "none" }} />
          </button>
        </div>

        {result && (
          <>
            <div className="pg-test-stats">
              {[
                { label: "latency", value: String(result.ms), unit: "ms" },
                {
                  label: "status",
                  value: result.ok ? "ok" : "err",
                  unit: result.ok ? "" : (result.error_type ?? ""),
                  color: result.ok ? "var(--neon)" : "var(--danger)",
                },
              ].map(({ label, value, unit, color }) => (
                <div key={label} className="pg-test-stat">
                  <div className="pg-test-stat-label">{label}</div>
                  <div
                    className="pg-test-stat-value"
                    style={color ? { color } : undefined}
                  >
                    {value}
                    <span>{unit}</span>
                  </div>
                </div>
              ))}
            </div>

            {result.error && (
              <div
                style={{
                  margin: "0 0 12px",
                  padding: "8px 10px",
                  borderRadius: 4,
                  border: "1px solid var(--danger)",
                  color: "var(--danger)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11.5,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {result.error}
              </div>
            )}

            <div className="ct-panel">
              <div className="ct-panel-head">
                <span className="ct-panel-title">raw response</span>
                <button
                  className="ct-btn-ghost"
                  style={{ padding: "4px 8px", fontSize: 11, color: copyError ? "var(--danger)" : undefined }}
                  onClick={copyResponse}
                >
                  {copied ? (
                    <Check width="11" height="11" />
                  ) : (
                    <Copy width="11" height="11" />
                  )}{" "}
                  {copyError ?? (copied ? "copied" : "copy")}
                </button>
              </div>
              <pre className={"pg-code pg-code-resp" + (result.ok ? " is-ok" : "")}>
                {responseBody}
              </pre>
            </div>

            <div className="ct-panel" style={{ marginTop: 12 }}>
              <div className="ct-panel-head">
                <span className="ct-panel-title">output schema</span>
                <span className="ct-eyebrow">declared by tool</span>
              </div>
              <div className="ct-panel-body">
                <SchemaTree schema={selected.output} />
              </div>
            </div>
          </>
        )}

        {!result && (
          <div
            style={{
              padding: "60px 0",
              textAlign: "center",
              color: "var(--fg-subtle)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
            }}
          >
            {firing ? "firing real tool call…" : "fire the tool to see the response"}
          </div>
        )}

        <div
          style={{
            display: "flex",
            gap: 8,
            marginTop: "auto",
            paddingTop: 12,
          }}
        >
          <button
            className="ct-btn-ghost"
            style={{ fontSize: 11.5 }}
            type="button"
            onClick={() => {
              if (!responseBody || !selected) return
              const blob = new Blob([responseBody], { type: "application/json" })
              const url = URL.createObjectURL(blob)
              const a = document.createElement("a")
              a.href = url
              a.download = `${selected.name}-run.json`
              a.click()
              URL.revokeObjectURL(url)
            }}
            disabled={!responseBody}
          >
            <Download width="12" height="12" /> Save run
          </button>
        </div>
      </aside>
    </div>
  )
}
