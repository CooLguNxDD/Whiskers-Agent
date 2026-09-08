/**
 * Model Role Ladders configuration section — per-node LLM selection ladders
 * (core_graph/model_roles/) plus the fleet-wide effort_map. Behind
 * MODEL_ROLES_ENABLED (default off) at the graph level; this UI is always
 * visible so operators can inspect/prepare ladders before flipping the flag.
 */
import { useState, type FC } from "react"
import {
  useModelRolesQuery,
  useSaveModelRoleMutation,
  useSaveEffortMapMutation,
  useDeleteModelRoleMutation,
} from "@/hooks/useConfig"
import type { ModelRoleEntry, ModelRoleRung } from "@/api/config"
import { EFFORT_LEVELS, SELECTOR_ALIASES, CORE_ROLES } from "@/components/goap/modelRoles.gen"
import { getErrorMessage } from "@/utils/errors"

const TERMINAL_FALLBACKS = ["ctx_llm", "none", "error"] as const

function selectorOptions(poolNames: string[]): string[] {
  const efforts = EFFORT_LEVELS.map((e) => `effort:${e}`)
  return [...SELECTOR_ALIASES, ...efforts, ...poolNames]
}

let nextRungId = 1
function ensureRungIds(ladder: ModelRoleRung[]): (ModelRoleRung & { _clientId?: string })[] {
  return ladder.map((rung) => ({
    ...rung,
    _clientId: (rung as { _clientId?: string })._clientId ?? `rung-${nextRungId++}`,
  }))
}

const ModelRoleSection: FC = () => {
  const { data, isPending } = useModelRolesQuery()
  const saveRole = useSaveModelRoleMutation()
  const saveEffortMap = useSaveEffortMapMutation()
  const deleteRole = useDeleteModelRoleMutation()

  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null)
  const [draft, setDraft] = useState<ModelRoleEntry | null>(null)

  if (isPending || !data) {
    return (
      <div className="ct-form-section">
        <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>Loading…</div>
      </div>
    )
  }

  const roleMap = new Map(data.roles.map((r) => [r.role_id, r]))
  // CORE_ROLES (generated) is the authoritative list/order; merge in the
  // server's effective spec (source/preview/db-edited ladder) per role.
  const rows = CORE_ROLES.map((cr) => roleMap.get(cr.id) ?? null).filter(
    (r): r is ModelRoleEntry => r !== null,
  )

  const selected = selectedRoleId ? (draft ?? roleMap.get(selectedRoleId) ?? null) : null

  function selectRole(roleId: string) {
    setSelectedRoleId(roleId)
    const role = roleMap.get(roleId) ?? null
    setDraft(role ? { ...role, ladder: ensureRungIds(role.ladder) } : null)
    saveRole.reset()
  }

  function updateDraft(patch: Partial<ModelRoleEntry>) {
    if (saveRole.isError) saveRole.reset()
    setDraft((d) => (d ? { ...d, ...patch } : d))
  }

  function updateRung(index: number, patch: Partial<ModelRoleRung>) {
    if (!draft) return
    const ladder = draft.ladder.map((r, i) => (i === index ? { ...r, ...patch } : r))
    updateDraft({ ladder })
  }

  function addRung() {
    if (!draft) return
    updateDraft({
      ladder: [
        ...draft.ladder,
        { selector: "core", max_attempts: 1, timeout_s: null, _clientId: `rung-${nextRungId++}` } as ModelRoleRung,
      ],
    })
  }

  function removeRung(index: number) {
    if (!draft) return
    updateDraft({ ladder: draft.ladder.filter((_, i) => i !== index) })
  }

  function moveRung(index: number, dir: -1 | 1) {
    if (!draft) return
    const target = index + dir
    if (target < 0 || target >= draft.ladder.length) return
    const ladder = [...draft.ladder]
    ;[ladder[index], ladder[target]] = [ladder[target], ladder[index]]
    updateDraft({ ladder })
  }

  function handleSave() {
    if (!draft) return
    const cleanLadder = draft.ladder.map(({ selector, max_attempts, timeout_s }) => ({
      selector,
      max_attempts,
      timeout_s,
    }))
    saveRole.mutate(
      {
        roleId: draft.role_id,
        spec: {
          role_id: draft.role_id,
          description: draft.description,
          ladder: cleanLadder,
          validate: draft.validate,
          entry_conditions: draft.entry_conditions,
          escalate_on_exception: draft.escalate_on_exception,
          escalate_on_invalid: draft.escalate_on_invalid,
          terminal_fallback: draft.terminal_fallback,
        },
      },
      { onSuccess: (res) => res.role && setDraft({ ...res.role, ladder: ensureRungIds(res.role.ladder) }) },
    )
  }

  function handleReset() {
    if (!selectedRoleId) return
    deleteRole.mutate(selectedRoleId, {
      onSuccess: (res) => res.role && setDraft({ ...res.role, ladder: ensureRungIds(res.role.ladder) }),
    })
  }

  return (
    <div className="ct-form-section">
      <h3>Model Role Ladders</h3>
      <p className="sub">
        Per-node LLM escalation ladders (triage, planner, summary, specialist stack, …). Effective
        only once <code>MODEL_ROLES_ENABLED</code> is on — until then this is an audit/staging surface.
      </p>

      <div style={{ marginTop: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Effort Map</div>
        <p className="sub" style={{ marginTop: 0 }}>
          Fleet-wide knob: every plugin manifest's <code>effort</code>/<code>effort_overrides</code> key
          routes through this table (<code>effort:&lt;level&gt;</code> selectors too).
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
          {EFFORT_LEVELS.map((level) => (
            <div key={level} className="ct-field" style={{ minWidth: 160 }}>
              <label htmlFor={`effort-${level}`}>{level}</label>
              <select
                id={`effort-${level}`}
                className="ct-input"
                style={{ background: "var(--bg-input)" }}
                value={data.effort_map?.[level] ?? ""}
                onChange={(e) =>
                  saveEffortMap.mutate({ ...(data.effort_map ?? {}), [level]: e.target.value })
                }
              >
                <option value="" disabled>
                  — unset —
                </option>
                {SELECTOR_ALIASES.filter((a) => a !== "core").map((alias) => (
                  <option key={alias} value={alias}>
                    {alias}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
        {saveEffortMap.isError && (
          <div style={{ color: "var(--danger)", fontSize: 12, fontWeight: 600, marginTop: 8 }}>
            {getErrorMessage(saveEffortMap.error, "Failed to save effort_map.")}
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 20, marginTop: 20 }}>
        <div style={{ minWidth: 200 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Roles</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {rows.map((r) => (
              <button
                key={r.role_id}
                type="button"
                className="ct-btn-ghost"
                style={{
                  justifyContent: "space-between",
                  display: "flex",
                  fontWeight: selectedRoleId === r.role_id ? 700 : 400,
                }}
                onClick={() => selectRole(r.role_id)}
                aria-current={selectedRoleId === r.role_id ? "true" : undefined}
              >
                <span>{r.role_id}</span>
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    borderRadius: 8,
                    background: r.source === "db" ? "var(--neon-soft)" : "var(--bg-input)",
                    color: r.source === "db" ? "var(--neon)" : "var(--fg-subtle)",
                  }}
                >
                  {r.source}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div style={{ flex: 1 }}>
          {!selected ? (
            <div style={{ color: "var(--fg-subtle)", fontSize: 12 }}>Select a role to edit its ladder.</div>
          ) : (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{selected.role_id}</div>
                {selected.source === "db" && (
                  <button type="button" className="ct-btn-ghost" onClick={handleReset} disabled={deleteRole.isPending}>
                    Reset to default
                  </button>
                )}
              </div>
              <p className="sub" style={{ marginTop: 4 }}>{selected.description}</p>

              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
                {selected.ladder.map((rung, i) => (
                  <div key={(rung as { _clientId?: string })._clientId ?? i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <span style={{ fontSize: 11, color: "var(--fg-subtle)", width: 20 }}>#{i}</span>
                    <select
                      className="ct-input"
                      style={{ flex: 1, background: "var(--bg-input)" }}
                      value={rung.selector}
                      onChange={(e) => updateRung(i, { selector: e.target.value })}
                      aria-label={`Rung ${i} selector`}
                    >
                      {selectorOptions(data.pool_names).map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                    <span style={{ fontSize: 11, color: "var(--fg-subtle)", minWidth: 90 }}>
                      → {selected.preview[i]?.resolved_model ?? "?"}
                    </span>
                    <button
                      type="button"
                      className="ct-btn-ghost"
                      onClick={() => moveRung(i, -1)}
                      disabled={i === 0}
                      aria-label="Move rung up"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="ct-btn-ghost"
                      onClick={() => moveRung(i, 1)}
                      disabled={i === selected.ladder.length - 1}
                      aria-label="Move rung down"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="ct-btn-ghost"
                      style={{ color: "var(--danger)" }}
                      onClick={() => removeRung(i)}
                      disabled={selected.ladder.length <= 1}
                      aria-label="Remove rung"
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button type="button" className="ct-btn-ghost" style={{ alignSelf: "flex-start" }} onClick={addRung}>
                  Add rung
                </button>
              </div>

              <div style={{ marginTop: 16 }}>
                <div className="ct-field">
                  <label htmlFor="terminal-fallback">Terminal fallback</label>
                  <select
                    id="terminal-fallback"
                    className="ct-input"
                    style={{ background: "var(--bg-input)" }}
                    value={selected.terminal_fallback}
                    onChange={(e) =>
                      updateDraft({ terminal_fallback: e.target.value as ModelRoleEntry["terminal_fallback"] })
                    }
                  >
                    {TERMINAL_FALLBACKS.map((tf) => (
                      <option key={tf} value={tf}>
                        {tf}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="ct-checkbox-row" style={{ marginTop: 10 }}>
                  <div>
                    <div className="label">Escalate on invalid output</div>
                    <div className="desc">Advance to the next rung when the declared Validation fails.</div>
                  </div>
                  <button
                    className={"ct-switch " + (selected.escalate_on_invalid ? "is-on" : "")}
                    onClick={() => updateDraft({ escalate_on_invalid: !selected.escalate_on_invalid })}
                    role="switch"
                    aria-checked={selected.escalate_on_invalid}
                    aria-label="Toggle escalate on invalid output"
                  />
                </div>
                <div className="ct-checkbox-row">
                  <div>
                    <div className="label">Escalate on exception</div>
                    <div className="desc">Advance to the next rung when a rung's attempt raises.</div>
                  </div>
                  <button
                    className={"ct-switch " + (selected.escalate_on_exception ? "is-on" : "")}
                    onClick={() => updateDraft({ escalate_on_exception: !selected.escalate_on_exception })}
                    role="switch"
                    aria-checked={selected.escalate_on_exception}
                    aria-label="Toggle escalate on exception"
                  />
                </div>
              </div>

              {saveRole.isError && (
                <div style={{ color: "var(--danger)", fontSize: 12, fontWeight: 600, marginTop: 8 }}>
                  {getErrorMessage(saveRole.error, "Failed to save model-role spec.")}
                </div>
              )}
              {saveRole.isSuccess && (
                <div style={{ color: "var(--neon)", fontSize: 12, fontWeight: 600, marginTop: 8 }}>Saved</div>
              )}

              <button
                type="button"
                className="ct-btn-primary"
                style={{ marginTop: 14 }}
                onClick={handleSave}
                disabled={saveRole.isPending}
              >
                Save ladder
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * ModelRoleSection Component.
 * Renders the UI and handles state for per-node model-role ladders + effort_map.
 */
export default ModelRoleSection
