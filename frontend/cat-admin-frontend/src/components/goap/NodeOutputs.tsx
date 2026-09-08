import React from "react"
import type { Candidate, PlanStep, ContextEntry, ExecStep, SimState } from "./types"
import { FormattedMarkdown } from "../playground/FormattedMarkdown"

/**
 * Key-value display component.
 */
export const KV = ({ k, v, vColor }: { k: string; v: string | number; vColor?: string }) => (
  <div style={{ display: "flex", gap: 8, marginBottom: 4, fontSize: 12, fontFamily: "var(--font-mono)" }}>
    <span style={{ color: "var(--fg-subtle)", minWidth: 150, flexShrink: 0 }}>{k}</span>
    <span style={{ color: vColor || "var(--fg-muted)" }}>{v}</span>
  </div>
)

/**
 * Section container component.
 */
export const Sect = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div style={{ marginBottom: 14 }}>
    <div style={{ fontSize: 9.5, letterSpacing: "0.16em", textTransform: "uppercase",
      color: "var(--fg-subtle)", fontFamily: "var(--font-mono)",
      marginBottom: 7, paddingBottom: 5, borderBottom: "1px solid var(--hairline)" }}>
      {title}
    </div>
    {children}
  </div>
)



/**
 * Output display for embedder nodes.
 */
export function EmbedderOut({ out }: { out: Record<string, unknown> }) {
  const candidates = out.candidates as Candidate[]
  return (
    <>
      <Sect title="Search">
        <KV k="query" v={`"${out.query as string}"`} vColor="var(--neon)" />
        <KV k="routes_searched" v={out.searched as number} />
        <KV k="candidates_returned" v={candidates.length} />
      </Sect>
      <Sect title="Top candidates">
        {candidates.map((c, i) => (
          <div key={i} style={{ display: "flex", gap: 8, marginBottom: 5, fontSize: 12, fontFamily: "var(--font-mono)", alignItems: "center" }}>
            <span style={{ width: 42, textAlign: "right", fontWeight: 600, color: i === 0 ? "var(--amber)" : "var(--fg-muted)" }}>
              [{(c.score || 0).toFixed(2)}]
            </span>
            <span style={{ color: "var(--cyan)" }}>{c.route}</span>
            <span style={{ color: "var(--fg-subtle)", fontSize: 10, marginLeft: "auto" }}>{c.plugin}</span>
          </div>
        ))}
      </Sect>
    </>
  )
}

/**
 * Output display for planner nodes.
 */
export function PlannerOut({ out }: { out: Record<string, unknown> }) {
  const plan = out.instruction_set as PlanStep[]
  // parallel_groups: list of step-index lists that may execute concurrently.
  const groups = Array.isArray(out.parallel_groups) ? (out.parallel_groups as number[][]) : []
  // Map a step's array-position to its parallel group ordinal (only when the group has >1 member).
  const groupOf = (i: number): number | null => {
    const gi = groups.findIndex((g) => Array.isArray(g) && g.length > 1 && g.includes(i))
    return gi >= 0 ? gi + 1 : null
  }
  return (
    <>
      <Sect title="Plan">
        <KV k="plan_confidence" v={(out.plan_confidence as number || 0).toFixed(2)} vColor="var(--amber)" />
        <KV k="step_count" v={out.steps as number} />
        {groups.some((g) => Array.isArray(g) && g.length > 1) && (
          <KV k="parallel_groups" v={JSON.stringify(groups)} vColor="var(--cyan)" />
        )}
      </Sect>
      <Sect title="Instruction set">
        {plan.map((p, i) => {
          const grp = groupOf(i)
          return (
            <div key={i} style={{ background: "var(--bg-sunken)", border: "1px solid var(--hairline)",
              borderRadius: 7, padding: "8px 10px", marginBottom: 7, fontSize: 12, fontFamily: "var(--font-mono)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ color: "var(--amber)" }}>step_{p.step}</span>
                {p.model && (
                  <span style={{ fontSize: 10, color: "var(--cyan)", background: "rgba(0, 255, 255, 0.08)", padding: "1px 4px", borderRadius: 4 }}>{p.model}</span>
                )}
                {grp !== null && (
                  <span title="Runs concurrently with its parallel group" style={{ fontSize: 10, color: "var(--peach)", background: "rgba(255, 170, 100, 0.10)", padding: "1px 4px", borderRadius: 4, letterSpacing: "0.06em" }}>‖ grp {grp}</span>
                )}
              </div>
              <KV k="instruction" v={p.instruction} vColor="var(--fg)" />
              <KV k="operation_id" v={p.op} vColor="var(--cyan)" />
              <KV k="plugin_id" v={p.plugin} vColor="var(--fg-muted)" />
              {p.deps && <KV k="depends_on" v={p.deps.join(", ")} />}
            </div>
          )
        })}
      </Sect>
    </>
  )
}



/**
 * Output display for goal nodes.
 */
export function GoalOut({ out }: { out: Record<string, unknown> }) {
  const decision = out.decision as string
  const isDone = decision === "done"
  const achieved = Array.isArray(out.achieved_facts) ? (out.achieved_facts as string[]) : []
  const remaining = Array.isArray(out.remaining_goal_facts) ? (out.remaining_goal_facts as string[]) : []
  const goalFacts = Array.isArray(out.goal_facts) ? (out.goal_facts as string[]) : []
  return (
    <>
      <Sect title="GOAP Goal Tracking">
        <KV k="decision" v={decision || "evaluating..."} vColor={isDone ? "var(--ok)" : "var(--warn)"} />
        <KV k="iterations" v={`${out.iterations as number || 0} / ${out.max_iterations as number || 5}`} />
        {goalFacts.length > 0 && (
          <KV k="sub_goals" v={`${achieved.length} / ${goalFacts.length} satisfied`} vColor="var(--cyan)" />
        )}
        <div style={{ marginTop: 8, fontSize: 11, color: isDone ? "var(--ok)" : "var(--warn)", fontFamily: "var(--font-mono)", textTransform: "uppercase" }}>
          {isDone ? "✓ Goal achieved or limit reached. Terminating." : decision === "continue" ? "↺ Sub-goals remain. Re-planning the gap." : "↳ Evaluating completion..."}
        </div>
      </Sect>
      {achieved.length > 0 && (
        <Sect title="Achieved Sub-goals">
          {achieved.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: "var(--ok)", fontFamily: "var(--font-mono)", marginBottom: 4 }}>✓ {f}</div>
          ))}
        </Sect>
      )}
      {remaining.length > 0 && (
        <Sect title="Remaining Sub-goals">
          {remaining.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: "var(--warn)", fontFamily: "var(--font-mono)", marginBottom: 4 }}>○ {f}</div>
          ))}
        </Sect>
      )}
    </>
  )
}

/**
 * View component for session memory.
 */
export function SessionMemoryView({ state }: { state: SimState }) {
  const s = state.rawState || {}
  const goal = s.goal as string | undefined
  const wm = s.working_memory as Record<string, unknown> | undefined
  const iterations = (s.iterations as number) ?? 0
  const maxIterations = (s.max_iterations as number) ?? 5
  const lastSummary = (s.summary as string) || (s.last_summary as string)

  return (
    <div style={{ padding: "12px 16px", overflowY: "auto", height: "100%" }}>
      <Sect title="Overarching Goal">
        <div style={{ fontSize: 13, color: "var(--fg)", fontFamily: "var(--font-mono)", marginBottom: 8 }}>
          {goal ? `"${goal}"` : "No active goal set."}
        </div>
      </Sect>

      <Sect title="Loop Iterations">
        <KV k="current_iteration" v={iterations} />
        <KV k="max_iterations" v={maxIterations} />
        <div style={{ margin: "6px 0 10px 0" }}>
          <div style={{ width: "100%", height: 6, background: "var(--bg-sunken)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
            <div style={{ width: `${Math.min(100, (iterations / maxIterations) * 100)}%`, height: "100%", background: "var(--amber)" }} />
          </div>
        </div>
      </Sect>

      <Sect title="Working Memory (State Facts / Entities)">
        {!wm || Object.keys(wm).length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No facts or entities in working memory.</div>
        ) : (
          Object.entries(wm).map(([k, v]) => (
            <KV key={k} k={k} v={typeof v === "object" ? JSON.stringify(v) : String(v)} vColor="var(--cyan)" />
          ))
        )}
      </Sect>

      <Sect title="Last Summary">
        {lastSummary ? (
          <div style={{
            lineHeight: 1.6,
            fontSize: 12,
            color: "var(--fg-muted)",
            whiteSpace: "pre-wrap",
            background: "var(--bg-sunken)",
            padding: "8px 10px",
            borderRadius: "var(--radius)",
            border: "1px solid var(--hairline)",
            fontFamily: "var(--font-mono)"
          }}>
            {lastSummary}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No summary generated yet.</div>
        )}
      </Sect>
    </div>
  )
}

/**
 * Output display for decompose nodes.
 */
export function DecomposeOut({ out }: { out: Record<string, unknown> }) {
  const intent = out.intent as string || ""
  const subTasks = (out.sub_tasks as string[]) || []
  const seedValues = (out.seed_values as Record<string, unknown>) || {}
  return (
    <>
      {intent && (
        <Sect title="Decomposed Intent">
          <div style={{ fontSize: 13, color: "var(--fg)", fontFamily: "var(--font-mono)", marginBottom: 8 }}>
            "{intent}"
          </div>
        </Sect>
      )}
      <Sect title="Sub-tasks">
        {subTasks.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No sub-tasks generated.</div>
        ) : (
          subTasks.map((task, i) => (
            <div key={i} style={{ display: "flex", gap: 8, marginBottom: 5, fontSize: 12, fontFamily: "var(--font-mono)" }}>
              <span style={{ color: "var(--cyan)", fontWeight: 600 }}>{i + 1}.</span>
              <span style={{ color: "var(--fg-muted)" }}>{task}</span>
            </div>
          ))
        )}
      </Sect>
      <Sect title="Extracted Seed Values">
        {Object.keys(seedValues).length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No seed values extracted.</div>
        ) : (
          Object.entries(seedValues).map(([k, v]) => (
            <KV key={k} k={k} v={typeof v === "object" ? JSON.stringify(v) : String(v)} vColor="var(--amber)" />
          ))
        )}
      </Sect>
    </>
  )
}

/**
 * Output display for turn initialization nodes.
 */
export function TurnInitOut({ out }: { out: Record<string, unknown> }) {
  const query = out.query as string || ""
  return (
    <Sect title="Turn Initialization">
      <KV k="user_query" v={`"${query}"`} vColor="var(--cyan)" />
    </Sect>
  )
}

/**
 * Output display for triage router nodes.
 */
export function TriageOut({ out }: { out: Record<string, unknown> }) {
  const mode = out.mode as string || ""
  const modeColor = mode === "chat" ? "var(--pink)" : "var(--cyan)"
  return (
    <Sect title="Triage Router">
      <KV k="triage_mode" v={mode || "undetermined"} vColor={modeColor} />
    </Sect>
  )
}

/**
 * Output display for chat response nodes.
 */
export function ChatNodeOut({ out }: { out: Record<string, unknown> }) {
  const reply = out.reply as string || ""
  return (
    <Sect title="Chat Reply">
      <div style={{ background: "var(--bg-sunken)", border: "1px solid var(--hairline)",
        borderRadius: 8, padding: "10px 12px", fontFamily: "var(--font-mono)",
        fontSize: 12, lineHeight: 1.7, color: "var(--fg-muted)", whiteSpace: "pre-wrap" }}>
        {reply || "Empty chat response."}
      </div>
    </Sect>
  )
}

/**
 * Output display for context check nodes.
 */
export function ContextCheckOut({ out }: { out: Record<string, unknown> }) {
  const resolved = Array.isArray(out.resolved_args) ? (out.resolved_args as ContextEntry[]) : []
  const unresolved = Array.isArray(out.unresolved_required) ? (out.unresolved_required as string[]) : []
  const sourceColor: Record<string, string> = {
    "query literal": "var(--neon)", arg_binding: "var(--cyan)",
    context_params: "var(--amber)", "query NER": "var(--peach)", missing: "var(--danger)",
  }
  return (
    <>
      <Sect title={`Resolved Args (${resolved.length})`}>
        {resolved.length === 0 && unresolved.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No arguments resolved.</div>
        )}
        {resolved.map((r, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto",
            gap: 8, marginBottom: 6, fontSize: 12, fontFamily: "var(--font-mono)", alignItems: "center" }}>
            <span style={{ color: "var(--amber)" }}>step_{r.step}.{r.key}</span>
            <span style={{ color: r.value && String(r.value).startsWith("$") ? "var(--cyan)" : "var(--fg-muted)" }}>{r.value}</span>
            <span style={{ fontSize: 9.5, letterSpacing: "0.10em", textTransform: "uppercase",
              color: sourceColor[r.source] || "var(--fg-muted)", fontFamily: "var(--font-mono)" }}>
              {r.source}
            </span>
          </div>
        ))}
        {unresolved.map((u, i) => (
          <div key={i} style={{ fontSize: 12, color: "var(--danger)", fontFamily: "var(--font-mono)", marginBottom: 4 }}>
            ⚠️ Missing required: {u}
          </div>
        ))}
      </Sect>
    </>
  )
}

/**
 * Output display for confirmation nodes.
 */
export function ConfirmNodeOut({ out }: { out: Record<string, unknown> }) {
  const prompt = out.message as string || ""
  return (
    <Sect title="Confirmation Request Awaiting">
      <div style={{ background: "var(--ok-soft)", border: "1px solid color-mix(in oklch, var(--ok) 30%, transparent)",
        borderRadius: 8, padding: "10px 12px", fontFamily: "var(--font-mono)",
        fontSize: 12, lineHeight: 1.7, color: "var(--fg-muted)", whiteSpace: "pre-wrap" }}>
        {prompt || "Awaiting confirmation..."}
      </div>
    </Sect>
  )
}

/**
 * Output display for clarification nodes.
 */
export function ClarifyNodeOut({ out }: { out: Record<string, unknown> }) {
  const question = out.message as string || ""
  return (
    <Sect title="Clarification Question">
      <div style={{ background: "var(--bg-sunken)", border: "1px solid var(--hairline)",
        borderRadius: 8, padding: "10px 12px", fontFamily: "var(--font-mono)",
        fontSize: 12, lineHeight: 1.7, color: "var(--fg-muted)", whiteSpace: "pre-wrap" }}>
        {question || "Awaiting clarification..."}
      </div>
    </Sect>
  )
}

/**
 * Output display for step resolver nodes.
 */
export function StepResolverOut({ out }: { out: Record<string, unknown> }) {
  const plan = Array.isArray(out.plan) ? (out.plan as PlanStep[]) : []
  const currentIdx = out.current_step_index as number || 0
  return (
    <>
      <Sect title="Resolution State">
        <KV k="current_step_index" v={currentIdx} vColor="var(--amber)" />
      </Sect>
      <Sect title="Plan Steps">
        {plan.map((p, i) => (
          <div key={i} style={{ display: "flex", gap: 8, marginBottom: 5, fontSize: 12, fontFamily: "var(--font-mono)" }}>
            <span style={{ color: i === currentIdx ? "var(--amber)" : "var(--fg-subtle)", fontWeight: 600 }}>step_{p.step}</span>
            <span style={{ color: i === currentIdx ? "var(--fg)" : "var(--fg-muted)" }}>{p.instruction}</span>
          </div>
        ))}
      </Sect>
    </>
  )
}

/**
 * Output display for permission gate nodes.
 */
export function PermissionGateOut({ out }: { out: Record<string, unknown> }) {
  const scopes = Array.isArray(out.scopes) ? (out.scopes as string[]) : []
  return (
    <Sect title="Caller Permission Scopes">
      {scopes.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No specific permission scopes required (local/stdio mode).</div>
      ) : (
        scopes.map((scope, i) => (
          <div key={i} style={{ fontSize: 12, color: "var(--cyan)", fontFamily: "var(--font-mono)", marginBottom: 4 }}>• {scope}</div>
        ))
      )}
    </Sect>
  )
}

/**
 * Output display for builder nodes.
 */
export function BuilderOut({ out }: { out: Record<string, unknown> }) {
  const yaml = out.yaml as string || ""
  const plan_id = out.plan_id as string || ""
  const model = out.model as string || ""
  return (
    <Sect title="Workflow Compilation">
      {plan_id && <KV k="plan_id" v={plan_id} vColor="var(--amber)" />}
      {model && <KV k="compiler_model" v={model} vColor="var(--cyan)" />}
      {yaml && (
        <div style={{ fontSize: 10, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)", marginTop: 4 }}>
          {yaml.split("\n").length} lines compiled successfully. (See YAML PLAN tab)
        </div>
      )}
    </Sect>
  )
}

/**
 * Output display for wait nodes.
 */
export function WaitNodeOut({ out }: { out: Record<string, unknown> }) {
  const step_results = Array.isArray(out.step_results) ? (out.step_results as ExecStep[]) : []
  return (
    <Sect title="Async Wait Steps">
      {step_results.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No async wait steps currently executing.</div>
      ) : (
        step_results.map((ex, i) => (
          <div key={i} style={{ background: "var(--bg-sunken)", border: "1px solid var(--hairline)",
            borderRadius: 7, padding: "8px 10px", marginBottom: 7, fontFamily: "var(--font-mono)", fontSize: 12 }}>
            <span style={{ color: "var(--amber)" }}>step_{ex.step} (polling)</span>
            <div>{ex.method} {ex.path}</div>
          </div>
        ))
      )}
    </Sect>
  )
}

/**
 * Output display for executor nodes.
 */
export function ExecutorOut({ out }: { out: Record<string, unknown> }) {
  const step_results = Array.isArray(out.step_results) ? (out.step_results as ExecStep[]) : []
  return (
    <Sect title="Execution Results">
      {step_results.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>No executions yet.</div>
      ) : (
        step_results.map((ex, i) => (
          <div key={i} style={{ background: ex.error ? "var(--danger-soft)" : "var(--bg-sunken)",
            border: `1px solid ${ex.error ? "color-mix(in oklch, var(--danger) 30%, transparent)" : "var(--hairline)"}`,
            borderRadius: 7, padding: "8px 10px", marginBottom: 7, fontFamily: "var(--font-mono)", fontSize: 12 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 4 }}>
              <span style={{ color: "var(--amber)" }}>step_{ex.step}</span>
              {ex.model && <span style={{ fontSize: 10, color: "var(--cyan)", background: "rgba(0, 255, 255, 0.08)", padding: "1px 4px", borderRadius: 4 }}>{ex.model}</span>}
              <span style={{ marginLeft: "auto", color: ex.error ? "var(--danger)" : "var(--fg-subtle)" }}>{ex.latency}ms</span>
            </div>
            <div style={{ color: "var(--fg-muted)" }}>
              <span style={{ color: "var(--cyan)" }}>{ex.method}</span> <span>{ex.path}</span>
            </div>
            <div style={{ marginTop: 4, color: ex.error ? "var(--danger)" : "var(--ok)" }}>{ex.status} {ex.error || "OK"}</div>
          </div>
        ))
      )}
    </Sect>
  )
}

/**
 * Output display for validator nodes.
 */
export function ValidatorOut({ out }: { out: Record<string, unknown> }) {
  const summary = out.summary as string || ""
  return (
    <Sect title="Plan Validation">
      <div style={{
        lineHeight: 1.6,
        fontSize: 13,
        color: "var(--fg-muted)",
        whiteSpace: "pre-wrap",
        background: "var(--bg-sunken)",
        padding: "10px 12px",
        borderRadius: "var(--radius)",
        border: "1px solid var(--hairline)"
      }}>
        {summary || "Plan successfully verified by validator node."}
      </div>
    </Sect>
  )
}

/**
 * Output display for retry nodes.
 */
export function RetryNodeOut({ out }: { out: Record<string, unknown> }) {
  const retry_count = out.retry_count as number || 0
  return (
    <Sect title="Error Recovery Retry">
      <KV k="retry_attempt_count" v={retry_count} vColor="var(--warn)" />
    </Sect>
  )
}

/**
 * Output display for step dispatcher nodes.
 */
export function StepDispatcherOut({ out }: { out: Record<string, unknown> }) {
  const currentIdx = out.current_step_index as number || 0
  return (
    <Sect title="Step Dispatcher">
      <KV k="dispatching_step_index" v={currentIdx} vColor="var(--cyan)" />
    </Sect>
  )
}

/**
 * Output display for round summary nodes.
 */
export function RoundSummaryOut({ out }: { out: Record<string, unknown> }) {
  const note = (out.note as Record<string, unknown>) || {}
  const ops = Array.isArray(note.ops) ? (note.ops as string[]) : []
  // Backend stores findings as a char-capped JSON string (append_research_note);
  // keep array branch for forward-compat.
  const findingsRaw = note.findings
  const findingsList = Array.isArray(findingsRaw)
    ? (findingsRaw as string[]).map(String).filter(Boolean)
    : []
  const findingsText =
    typeof findingsRaw === "string" && findingsRaw.trim() ? findingsRaw.trim() : ""
  const hasFindings = findingsList.length > 0 || Boolean(findingsText)
  return (
    <Sect title="Round Summary">
      <KV k="iteration" v={(note.iteration as number) || 1} vColor="var(--amber)" />
      {!!note.directive && <KV k="directive" v={String(note.directive)} vColor="var(--cyan)" />}
      {ops.length > 0 && <KV k="ops_executed" v={ops.join(", ")} />}
      {hasFindings && (
        <Sect title="Round Findings">
          {findingsList.length > 0
            ? findingsList.map((f, i) => (
                <div key={i} style={{ fontSize: 12, color: "var(--fg-muted)", fontFamily: "var(--font-mono)", marginBottom: 4 }}>
                  • {f}
                </div>
              ))
            : (
                <div style={{ fontSize: 12, color: "var(--fg-muted)", fontFamily: "var(--font-mono)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {findingsText}
                </div>
              )}
        </Sect>
      )}
    </Sect>
  )
}

/**
 * Output display for summary nodes.
 */
export function SummaryNodeOut({ out }: { out: Record<string, unknown> }) {
  // undefined = still generating; empty string = finished with no text.
  const summary = out.summary as string | undefined
  return (
    <Sect title="Summary Generation">
      <div style={{
        lineHeight: 1.6,
        fontSize: 13,
        color: "var(--fg-muted)",
        background: "var(--bg-sunken)",
        padding: "10px 12px",
        borderRadius: "var(--radius)",
        border: "1px solid var(--hairline)"
      }}>
        {summary === undefined
          ? "Generating summary..."
          : summary
            ? <FormattedMarkdown content={summary} />
            : null}
      </div>
    </Sect>
  )
}

