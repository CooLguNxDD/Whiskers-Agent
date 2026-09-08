/**
 * Pure derivation of GOAP simulation state from a merged graph-state snapshot.
 * Extracted from the old goapStore so the DAG can be rendered live (during
 * streaming) or replayed (from a frozen per-message snapshot) without a store.
 */

import type { NodeId, SimNodeState, SimState } from "./types"
import { NODE_REGISTRY } from "./NodeRegistry"

/** Downstream nodes marked skipped when the operator cancels a confirmation. */
const CANCEL_SKIPPED: NodeId[] = ["builder", "executor", "wait_node", "validator", "retry_node", "step_dispatcher", "summary_node"]

/** Task-path nodes skipped when triage routes to conversational chat. */
const CHAT_SKIPPED: NodeId[] = [
  "decompose",
  "embedder",
  "planner",
  "context_check",
  "confirm_node",
  "clarify_node",
  "step_resolver",
  "permission_gate",
  "builder",
  "wait_node",
  "executor",
  "validator",
  "retry_node",
  "step_dispatcher",
  "summary_node",
  "goap_goal",
]

export interface DerivedSim {
  nodes: Record<NodeId, SimNodeState>
  activeNode: NodeId | null
  yamlPlan: string | null
  awaiting: SimState["awaiting"]
  done: boolean
  result: SimState["result"]
}

const mkNode = (): SimNodeState => ({ status: "idle", output: null })

/**
 * Derives the full node/status picture from a merged state snapshot.
 * Set `merged.__cancelled` truthy to render the operator-cancelled path.
 */
export function buildSimState(merged: Record<string, unknown>): DerivedSim {
  const nodes = Object.fromEntries(
    NODE_REGISTRY.map((def) => [def.id, mkNode()])
  ) as Record<NodeId, SimNodeState>

  let activeNode: NodeId | null = null
  NODE_REGISTRY.forEach((def) => {
    const derived = def.derive(merged)
    if (derived) {
      if (derived.status === "done") activeNode = def.id
      nodes[def.id] = derived
    }
  })

  const liveId = (merged.active_node ?? merged.activeNode) as string | undefined
  if (liveId && liveId in nodes) {
    const id = liveId as NodeId
    const cur = nodes[id]
    if (cur.status === "idle") {
      nodes[id] = { ...cur, status: "active" }
      activeNode = id
    } else if (cur.status === "active") {
      activeNode = id
    }
  }

  let awaiting: SimState["awaiting"] = null
  let done = false
  let result: SimState["result"] = null
  const yamlPlan = (merged.yaml_workflow as string | undefined) ?? null

  if (merged.response) {
    const resp = merged.response as { status?: string; steps_executed?: number }
    if (resp.status === "confirmation_needed") {
      awaiting = "confirm"
    } else if (resp.status === "clarification_needed") {
      awaiting = "clarify"
      done = true
      result = { status: "clarification_needed" }
    } else if (resp.status === "ok") {
      done = true
      result = { status: "ok", plan_id: merged.workflow_plan_id as string, steps: resp.steps_executed }
    } else if (resp.status === "chat") {
      done = true
      result = { status: "chat" }
    } else if (resp.status === "error") {
      done = true
      result = { status: "error" }
    }
  }

  if (merged.__cancelled) {
    CANCEL_SKIPPED.forEach((id) => { nodes[id] = { status: "skipped", output: null } })
    awaiting = null
    done = true
    result = { status: "cancelled" }
  }

  if (merged.triage_mode === "chat") {
    CHAT_SKIPPED.forEach((id) => {
      nodes[id] = { status: "skipped", output: null }
    })
    if (CHAT_SKIPPED.includes(activeNode as NodeId)) {
      activeNode = nodes.chat_node?.status === "done" ? "chat_node" : activeNode
    }
  }

  return { nodes, activeNode, yamlPlan, awaiting, done, result }
}
