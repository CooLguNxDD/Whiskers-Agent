/**
 * Step Model Routing configuration section — strategy, task-type maps,
 * per-operation overrides, and parallel execution settings for GOAP steps.
 */
import { useState, type FC } from "react"
import {
  useStepModelPolicyQuery,
  useSaveStepModelPolicyMutation,
} from "@/hooks/useConfig"
import type { StepModelPolicy } from "@/api/config"
import { getErrorMessage } from "@/utils/errors"

const TASK_TYPES = ["data_retrieval", "reasoning", "formatting"] as const

const STRATEGY_HELP: Record<StepModelPolicy["strategy"], string> = {
  off: "Use the active chat LLM for every step.",
  strength: "Pick the highest-strength pool entry per step complexity.",
  task_type: "Route steps by inferred task type (retrieval, reasoning, formatting).",
  explicit: "Assign a specific pool entry to each operation_id.",
}

const StepModelSection: FC = () => {
  const { data, isPending } = useStepModelPolicyQuery()
  const save = useSaveStepModelPolicyMutation()

  const [newOpId, setNewOpId] = useState("")
  const [newOpModel, setNewOpModel] = useState("")

  if (isPending || !data?.policy) {
    return (
      <div className="ct-form-section">
        <div style={{ color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", fontSize: 12 }}>Loading…</div>
      </div>
    )
  }

  const policy = data.policy
  const poolNames = data.pool_names ?? []

  function update(patch: Partial<StepModelPolicy>) {
    save.mutate(patch)
  }

  function handleAddOverride() {
    const opId = newOpId.trim()
    if (!opId || !newOpModel) return
    update({ op_overrides: { ...policy.op_overrides, [opId]: newOpModel } })
    setNewOpId("")
    setNewOpModel("")
  }

  function handleRemoveOverride(opId: string) {
    const next = { ...policy.op_overrides }
    delete next[opId]
    update({ op_overrides: next })
  }

  return (
    <div className="ct-form-section">
      <h3>Step Model Routing</h3>
      <p className="sub">
        Control which LLM pool entry runs each GOAP plan step and whether independent steps execute in parallel.
      </p>

      {save.isError && (
        <div style={{ color: "var(--danger)", fontSize: 12, fontWeight: 600, marginTop: 8 }}>
          {getErrorMessage(save.error, "Failed to save step model policy.")}
        </div>
      )}
      {save.isSuccess && (
        <div style={{ color: "var(--neon)", fontSize: 12, fontWeight: 600, marginTop: 8 }}>Saved</div>
      )}

      <div className="ct-field" style={{ marginTop: 14 }}>
        <label htmlFor="step-model-strategy">Routing Strategy</label>
        <select
          id="step-model-strategy"
          className="ct-input"
          value={policy.strategy}
          onChange={(e) =>
            update({ strategy: e.target.value as StepModelPolicy["strategy"] })
          }
          style={{ background: "var(--bg-input)" }}
        >
          <option value="off">off</option>
          <option value="strength">strength</option>
          <option value="task_type">task_type</option>
          <option value="explicit">explicit</option>
        </select>
        <span style={{ fontSize: 11, color: "var(--fg-subtle)", marginTop: 4, display: "block" }}>
          {STRATEGY_HELP[policy.strategy]}
        </span>
      </div>

      {policy.strategy === "task_type" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 14 }}>
          {TASK_TYPES.map((tt) => (
            <div key={tt} className="ct-field">
              <label htmlFor={`task-type-${tt}`}>{tt}</label>
              <select
                id={`task-type-${tt}`}
                className="ct-input"
                value={policy.task_type_map[tt] ?? ""}
                onChange={(e) =>
                  update({
                    task_type_map: { ...policy.task_type_map, [tt]: e.target.value },
                  })
                }
                style={{ background: "var(--bg-input)" }}
              >
                <option value="">(default)</option>
                {poolNames.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}

      {policy.strategy === "explicit" && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Operation Overrides</div>
          {Object.keys(policy.op_overrides).length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--fg-subtle)", marginBottom: 10 }}>
              No overrides configured.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
              {Object.entries(policy.op_overrides).map(([opId, model]) => (
                <div key={opId} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    className="ct-input"
                    value={opId}
                    readOnly
                    style={{ flex: 1 }}
                    aria-label={`Operation ${opId}`}
                  />
                  <select
                    className="ct-input"
                    value={model}
                    onChange={(e) =>
                      update({
                        op_overrides: { ...policy.op_overrides, [opId]: e.target.value },
                      })
                    }
                    style={{ flex: 1, background: "var(--bg-input)" }}
                    aria-label={`Model for ${opId}`}
                  >
                    {poolNames.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="ct-btn-ghost"
                    style={{ padding: "4px 8px", fontSize: 12, height: 28, color: "var(--danger)" }}
                    onClick={() => handleRemoveOverride(opId)}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              className="ct-input"
              placeholder="operation_id"
              value={newOpId}
              onChange={(e) => setNewOpId(e.target.value)}
              style={{ flex: 1 }}
              aria-label="New operation id"
            />
            <select
              className="ct-input"
              value={newOpModel}
              onChange={(e) => setNewOpModel(e.target.value)}
              style={{ flex: 1, background: "var(--bg-input)" }}
              aria-label="New override model"
            >
              <option value="">Select model…</option>
              {poolNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="ct-btn-ghost"
              style={{ padding: "4px 12px", fontSize: 12, height: 28 }}
              onClick={handleAddOverride}
            >
              Add override
            </button>
          </div>
        </div>
      )}

      <div className="ct-checkbox-row" style={{ marginTop: 18 }}>
        <div>
          <div className="label">Parallel step execution</div>
          <div className="desc">
            Run independent GOAP steps concurrently when the plan allows it.
          </div>
        </div>
        <button
          className={"ct-switch " + (policy.parallel_enabled ? "is-on" : "")}
          onClick={() => update({ parallel_enabled: !policy.parallel_enabled })}
          role="switch"
          aria-checked={policy.parallel_enabled}
          aria-label="Toggle parallel step execution"
        />
      </div>

      <div className="ct-field" style={{ marginTop: 14 }}>
        <label htmlFor="fanout-concurrency">Fan-out concurrency</label>
        <input
          id="fanout-concurrency"
          className="ct-input"
          type="number"
          min={1}
          max={20}
          value={policy.fanout_concurrency}
          onChange={(e) => update({ fanout_concurrency: Number(e.target.value) })}
        />
        <span style={{ fontSize: 11, color: "var(--fg-subtle)", marginTop: 4, display: "block" }}>
          Maximum concurrent items when a step fans out over a collection (1–20).
        </span>
      </div>
    </div>
  )
}

/**
 * StepModelSection Component.
 * Renders the UI and handles state for the StepModelSection feature.
 */
export default StepModelSection