/**
 * model_audit overlay helpers — group the graph state's model_audit array
 * (core_graph.states.DynamicAPIState.model_audit, an
 * Annotated[list, operator.add] reducer) by the node that produced each
 * entry, purely client-side. No backend change needed: stream_graph_impl
 * already streams the full state snapshot (core_graph/mcp_tool.py), so
 * model_audit rides along in SimState.rawState whenever the model-role layer
 * is active. Entries are simply absent when MODEL_ROLES_ENABLED is off or
 * for an older session snapshot — every consumer here degrades to "no chip".
 */
import type { NodeId } from "./graphTopology.gen"
import { NODE_ROLES, type RoleId } from "./modelRoles.gen"

export interface ModelAuditRungAttempt {
  rung_index: number
  selector: string
  model: string
  status: "ok" | "invalid" | "error"
  reason: string | null
  elapsed_ms: number
}

export interface ModelAuditEntry {
  role: string
  node: string
  rung_index: number
  selector: string
  model: string
  status: "ok" | "invalid" | "error" | "exhausted"
  escalated: boolean
  reason: string | null
  attempts: ModelAuditRungAttempt[]
  elapsed_ms: number
}

/** Narrow an unknown raw state value into a well-typed model_audit array (never throws). */
function extractAudit(rawState: Record<string, unknown> | undefined): ModelAuditEntry[] {
  const raw = rawState?.model_audit
  if (!Array.isArray(raw)) return []
  return raw.filter((e): e is ModelAuditEntry => {
    if (!e || typeof e !== "object") return false
    const rec = e as Record<string, unknown>
    return "node" in rec && typeof rec.node === "string" && "model" in rec && typeof rec.model === "string"
  })
}

/**
 * Group model_audit entries by the graph node id that produced them.
 * Entries with an unrecognized/missing `node` field are silently dropped —
 * they can't be rendered as a per-node chip. A state with no model_audit at
 * all (flag off, or a snapshot predating this layer) yields `{}`.
 */
export function auditByNode(
  rawState: Record<string, unknown> | undefined,
): Partial<Record<NodeId, ModelAuditEntry[]>> {
  const out: Partial<Record<NodeId, ModelAuditEntry[]>> = {}
  for (const entry of extractAudit(rawState)) {
    const nodeId = entry.node as NodeId
    if (!nodeId) continue
    ;(out[nodeId] ??= []).push(entry)
  }
  return out
}

/** The role id(s) a node resolves through, from the generated node->role map. */
export function roleHintForNode(nodeId: NodeId): RoleId[] {
  return NODE_ROLES[nodeId] ?? []
}
