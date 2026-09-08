/**
 * ConfigTab Component
 *
 * Editor for a plugin's config.json settings.
 * - "base" is the on-disk file (read-only reference, declared by the plugin).
 * - "override" is a console-edited JSON blob persisted to meta.config (DB), applied on next plugin reload.
 * - Editing writes the full override object; "Revert to file default" clears the override.
 */
import { useEffect, useState, type FC } from "react"
import { cn } from "@/lib/utils"
import {
  usePluginConfigQuery,
  useSavePluginConfigMutation,
  useResetPluginConfigMutation,
} from "@/hooks/usePlugins"
import { getErrorMessage } from "@/utils/errors"

export interface ConfigTabProps {
  pluginId: string
  className?: string
}

const ConfigTab: FC<ConfigTabProps> = ({ pluginId, className }) => {
  const { data, isPending, isError } = usePluginConfigQuery(pluginId)
  const { mutate: save, isPending: isSaving } = useSavePluginConfigMutation(pluginId)
  const { mutate: reset, isPending: isResetting } = useResetPluginConfigMutation(pluginId)

  const [text, setText] = useState("")
  const [parseError, setParseError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  // Seed the editor buffer once the effective config loads (or plugin changes).
  useEffect(() => {
    if (data) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setText(JSON.stringify(data.effective ?? {}, null, 2))
    }
  }, [data, pluginId])

  useEffect(() => {
    if (!savedAt) return
    const t = setTimeout(() => setSavedAt(null), 2800)
    return () => clearTimeout(t)
  }, [savedAt])

  const isOverridden = !!data?.override

  const handleSave = () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch (err) {
      setParseError(getErrorMessage(err))
      return
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setParseError("Config must be a JSON object")
      return
    }
    setParseError(null)
    setSaveError(null)
    save(parsed as Record<string, unknown>, {
      onSuccess: () => setSavedAt(Date.now()),
      onError: (err) => setSaveError(getErrorMessage(err)),
    })
  }

  const handleRevert = () => {
    if (!confirm("Revert to the on-disk config.json default? This clears your console override.")) return
    reset(undefined, {
      onSuccess: () => setSavedAt(Date.now()),
      onError: (err) => setSaveError(getErrorMessage(err)),
    })
  }

  if (isPending) {
    return (
      <div className={cn("ct-panel", className)}>
        <div className="ct-panel-body" style={{ color: "var(--fg-subtle)", fontSize: 12 }}>
          Loading config…
        </div>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className={cn("ct-panel", className)}>
        <div className="ct-panel-body" style={{ color: "var(--danger)", fontSize: 12 }}>
          Failed to load plugin config.
        </div>
      </div>
    )
  }

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>
          Editing <code>{data.config_filename}</code> for this plugin. Overrides are stored in the
          console DB and applied on the plugin&apos;s next reload — they don&apos;t touch the file on disk.
        </div>
        {isOverridden && (
          <span className="ct-pill is-amber" style={{ fontFamily: "var(--font-mono)", flexShrink: 0 }}>
            override active
          </span>
        )}
      </div>

      {data.base === null && (
        <div
          style={{
            padding: "10px 13px",
            background: "color-mix(in oklch, var(--danger) 6%, transparent)",
            border: "1px solid color-mix(in oklch, var(--danger) 22%, var(--hairline))",
            borderRadius: 8,
            fontFamily: "var(--font-mono)",
            fontSize: 11.5,
            color: "var(--fg-muted)",
          }}
        >
          No <code>{data.config_filename}</code> found on disk for this plugin — starting from an empty object.
        </div>
      )}

      <div className="ct-panel">
        <div className="ct-panel-body" style={{ paddingTop: 8 }}>
          <textarea
            className="ct-input"
            style={{
              width: "100%",
              minHeight: 320,
              fontFamily: "var(--font-mono)",
              fontSize: 11.5,
              lineHeight: 1.45,
              resize: "vertical",
              boxSizing: "border-box",
            }}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              setParseError(null)
            }}
            spellCheck={false}
          />
          {parseError && (
            <div style={{ fontSize: 11.5, color: "var(--danger)", marginTop: 6, fontFamily: "var(--font-mono)" }}>
              {parseError}
            </div>
          )}
          {saveError && (
            <div style={{ fontSize: 11.5, color: "var(--danger)", marginTop: 6, fontFamily: "var(--font-mono)" }}>
              {saveError}
            </div>
          )}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="ct-btn-primary" disabled={isSaving} onClick={handleSave}>
          {isSaving ? "Saving…" : "Save changes"}
        </button>
        <button className="ct-btn-ghost" disabled={isResetting || !isOverridden} onClick={handleRevert}>
          {isResetting ? "Reverting…" : "Revert to file default"}
        </button>
        {savedAt && (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11.5,
              color: "var(--neon)",
              letterSpacing: "0.08em",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            saved
          </span>
        )}
      </div>
    </div>
  )
}

/**
 * ConfigTab Component.
 * Renders the UI and handles state for the ConfigTab feature.
 */
export default ConfigTab
