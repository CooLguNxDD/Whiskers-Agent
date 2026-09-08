/**
 * SkillsTab Component
 *
 * Editor for plugin skill files persisted in the DB (meta.skills).
 * - Seeded automatically from manifest-declared FS files on first plugin load ("initial load").
 * - DB wins over FS for planner prompt injection (_format_plugin_skills + skill_registry).
 * - Supports add / modify / delete of skill "files" (key + markdown content).
 * - "Load from disk" overwrites the DB entry from the package file and shows sha256 drift.
 * Content is injected into GOAP/linear planner when the plugin's ops appear in candidates.
 */
import { useState, type FC } from "react"
import { cn } from "@/lib/utils"
import {
  usePluginSkillsQuery,
  useSavePluginSkillMutation,
  useDeletePluginSkillMutation,
  useReloadPluginSkillsMutation,
} from "@/hooks/usePlugins"
import type { PluginSkill } from "@/api/plugins"

export interface SkillsTabProps {
  pluginId: string
  className?: string
}

/** Shorten sha256:abcdef… for display. */
function shortHash(h?: string | null): string {
  if (!h) return "—"
  const hex = h.startsWith("sha256:") ? h.slice(7) : h
  return hex.length > 12 ? `${hex.slice(0, 12)}…` : hex
}

const SkillsTab: FC<SkillsTabProps> = ({ pluginId, className }) => {
  const { data, isPending, refetch } = usePluginSkillsQuery(pluginId)
  const { mutate: saveSkill, isPending: isSaving } = useSavePluginSkillMutation(pluginId)
  const { mutate: deleteSkill, isPending: isDeleting } = useDeletePluginSkillMutation(pluginId)
  const { mutate: reloadSkills, isPending: isReloading } = useReloadPluginSkillsMutation(pluginId)

  const skills: PluginSkill[] = data?.skills ?? []
  const [newKey, setNewKey] = useState("")
  const [newContent, setNewContent] = useState("")
  const [showAdd, setShowAdd] = useState(false)
  const [reloadError, setReloadError] = useState<string | null>(null)

  // local editing buffers per key (to avoid committing partial edits)
  const [buffers, setBuffers] = useState<Record<string, string>>({})

  const getBuffer = (key: string, fallback: string) => buffers[key] ?? fallback

  const setBuffer = (key: string, val: string) => {
    setBuffers((b) => ({ ...b, [key]: val }))
  }

  const isDirty = (key: string, original: string) => {
    const b = getBuffer(key, original)
    return b !== original
  }

  const handleSave = (key: string, content: string) => {
    setReloadError(null)
    saveSkill(
      { key, content },
      {
        onSuccess: () => {
          // clear buffer for that key
          setBuffers((b) => {
            const copy = { ...b }
            delete copy[key]
            return copy
          })
          void refetch()
        },
      }
    )
  }

  const handleDelete = (key: string) => {
    if (!confirm(`Delete skill "${key}"? This removes the DB override (may re-seed from FS on next load).`)) return
    setReloadError(null)
    deleteSkill(key, {
      onSuccess: () => {
        setBuffers((b) => {
          const copy = { ...b }
          delete copy[key]
          return copy
        })
        void refetch()
      },
    })
  }

  const handleLoadFromDisk = (key?: string) => {
    const label = key ?? "all listed skills"
    if (
      !confirm(
        key
          ? `Load "${key}" from the plugin package on disk?\nThis overwrites the DB copy with the file contents.`
          : `Load all skill files from disk into the database?\nThis overwrites DB copies for every key that exists on disk.`
      )
    ) {
      return
    }
    setReloadError(null)
    reloadSkills(key, {
      onSuccess: (res) => {
        // Drop local buffers for reloaded keys so textarea shows DB content
        const reloadedKeys = new Set((res.reloaded ?? []).map((r) => r.key))
        setBuffers((b) => {
          const copy = { ...b }
          for (const k of reloadedKeys) delete copy[k]
          return copy
        })
        if (res.failed?.length && !res.reloaded?.length) {
          setReloadError(res.failed.map((f) => `${f.key}: ${f.error}`).join("; "))
        } else if (res.failed?.length) {
          setReloadError(`Partial: ${res.failed.map((f) => `${f.key}: ${f.error}`).join("; ")}`)
        }
        void refetch()
      },
      onError: (err) => {
        setReloadError(err instanceof Error ? err.message : `Failed to load ${label} from disk`)
      },
    })
  }

  const handleAdd = () => {
    const k = newKey.trim()
    if (!k) return
    const c = newContent
    setReloadError(null)
    saveSkill(
      { key: k, content: c },
      {
        onSuccess: () => {
          setNewKey("")
          setNewContent("")
          setShowAdd(false)
          void refetch()
        },
      }
    )
  }

  if (isPending) {
    return (
      <div className={cn("ct-panel", className)}>
        <div className="ct-panel-body" style={{ color: "var(--fg-subtle)", fontSize: 12 }}>
          Loading skills…
        </div>
      </div>
    )
  }

  const busy = isSaving || isDeleting || isReloading
  const anyOnDisk = skills.some((s) => s.on_disk)

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>
        Skill files are stored in the database and injected into the goal-agent planner prompts
        (only for plugins present in the current turn&apos;s candidates). Declared in manifest.json
        for initial seeding. Use <strong>Load from disk</strong> to refresh DB from the package
        file (sha256 shown for validation).
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <button
          className="ct-btn-ghost"
          disabled={busy || !anyOnDisk}
          onClick={() => handleLoadFromDisk()}
          style={{ fontSize: 11, padding: "2px 8px" }}
          title="Overwrite all DB skills that have a matching package file"
        >
          {isReloading ? "Loading…" : "Load all from disk"}
        </button>
        {reloadError && (
          <span style={{ fontSize: 11, color: "var(--fg-danger)", fontFamily: "var(--font-mono)" }}>
            {reloadError}
          </span>
        )}
      </div>

      {/* list of existing skills */}
      {skills.length === 0 && (
        <div className="ct-panel">
          <div className="ct-panel-body" style={{ color: "var(--fg-subtle)", fontSize: 12 }}>
            No skill files yet. Add one below or declare in the plugin&apos;s manifest.json.
          </div>
        </div>
      )}

      {skills.map((s) => {
        const orig = s.content || ""
        const buf = getBuffer(s.key, orig)
        const dirty = isDirty(s.key, orig)
        return (
          <div key={s.key} className="ct-panel">
            <div className="ct-panel-head" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span className="ct-panel-title" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                {s.key}
              </span>
              {s.declared && (
                <span style={{ fontSize: 10, padding: "1px 6px", border: "1px solid var(--hairline)", borderRadius: 3 }}>
                  declared
                </span>
              )}
              {s.on_disk && s.in_sync && (
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    border: "1px solid var(--hairline)",
                    borderRadius: 3,
                    color: "var(--fg-subtle)",
                  }}
                  title={`DB ${s.content_hash ?? ""} = FS ${s.fs_content_hash ?? ""}`}
                >
                  in sync
                </span>
              )}
              {s.on_disk && !s.in_sync && (
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    border: "1px solid var(--fg-danger, #c44)",
                    borderRadius: 3,
                    color: "var(--fg-danger)",
                  }}
                  title={`DB ${s.content_hash ?? "empty"} ≠ FS ${s.fs_content_hash ?? "—"}`}
                >
                  out of sync
                </span>
              )}
              {!s.on_disk && (
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    border: "1px solid var(--hairline)",
                    borderRadius: 3,
                    color: "var(--fg-subtle)",
                  }}
                >
                  no disk file
                </span>
              )}
              <div style={{ flex: 1 }} />
              <button
                className="ct-btn-ghost"
                disabled={busy || !s.on_disk}
                onClick={() => handleLoadFromDisk(s.key)}
                style={{ fontSize: 11, padding: "2px 8px" }}
                title={
                  s.on_disk
                    ? `Load from disk (FS ${shortHash(s.fs_content_hash)})`
                    : "No matching file under plugins/<id>/"
                }
              >
                {isReloading ? "Loading…" : "Load from disk"}
              </button>
              <button
                className="ct-btn-ghost"
                disabled={isSaving || isDeleting || isReloading || !dirty}
                onClick={() => handleSave(s.key, buf)}
                style={{ fontSize: 11, padding: "2px 8px" }}
              >
                {isSaving ? "Saving…" : "Save"}
              </button>
              <button
                className="ct-btn-ghost"
                disabled={isDeleting || isReloading}
                onClick={() => handleDelete(s.key)}
                style={{ fontSize: 11, padding: "2px 8px", color: "var(--fg-danger)" }}
              >
                Delete
              </button>
            </div>
            <div
              style={{
                fontSize: 10,
                fontFamily: "var(--font-mono)",
                color: "var(--fg-subtle)",
                padding: "0 12px 4px",
              }}
              title="sha256 of skill body (frontmatter stripped)"
            >
              db {shortHash(s.content_hash)} · fs {shortHash(s.fs_content_hash)}
            </div>
            <div className="ct-panel-body" style={{ paddingTop: 8 }}>
              <textarea
                className="ct-input"
                style={{
                  width: "100%",
                  minHeight: 160,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  lineHeight: 1.35,
                  resize: "vertical",
                }}
                value={buf}
                onChange={(e) => setBuffer(s.key, e.target.value)}
                placeholder="Markdown content for planner guidance…"
              />
              {dirty && <div style={{ fontSize: 10, color: "var(--fg-subtle)", marginTop: 4 }}>unsaved changes</div>}
            </div>
          </div>
        )
      })}

      {/* add new */}
      <div className="ct-panel">
        <div className="ct-panel-head">
          <button
            className="ct-btn-ghost"
            onClick={() => {
              setShowAdd(!showAdd)
              if (!showAdd) {
                setNewKey("")
                setNewContent("")
              }
            }}
            style={{ fontSize: 12 }}
          >
            {showAdd ? "Cancel" : "+ Add skill file"}
          </button>
        </div>
        {showAdd && (
          <div className="ct-panel-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <input
              className="ct-input"
              style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}
              placeholder="skills/my-workflow/SKILL.md"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
            />
            <textarea
              className="ct-input"
              style={{ fontFamily: "var(--font-mono)", fontSize: 11, minHeight: 120 }}
              placeholder="Markdown content…"
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
            />
            <div>
              <button
                className="ct-btn"
                disabled={!newKey.trim() || isSaving}
                onClick={handleAdd}
                style={{ fontSize: 12 }}
              >
                {isSaving ? "Adding…" : "Add & Save"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * SkillsTab Component.
 * Renders the UI and handles state for the SkillsTab feature.
 */
export default SkillsTab
