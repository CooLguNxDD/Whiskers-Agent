import type { ReactNode } from "react"

export interface Candidate { route: string; score: number; plugin: string }
export interface PlanStep {
  step: number; instruction: string; op: string; plugin: string;
  deps?: string[]; bindings?: Record<string, string>; path_params?: Record<string, string>;
  model?: string
}
export interface ContextEntry { step: number; key: string; value: string; source: string }
export interface ExecStep {
  step: number; attempt?: number; method: string; path: string;
  status: number; latency: number; data?: Record<string, unknown>; error?: string; model?: string
}
export interface ValidationStep { step: number; ok: boolean; status?: number; error?: string; note?: string }

export interface SimNodeState { status: "idle" | "active" | "done" | "error" | "skipped" | "waiting"; output: unknown }
import type { NodeId } from "./graphTopology.gen"
export type { NodeId }

/**
 * Node definition interface for the GOAP visualization.
 */
export interface NodeDef {
  id: NodeId
  label: string; short: string
  derive: (state: Record<string, unknown>) => { status: SimNodeState["status"]; output: unknown } | null
  render: (output: Record<string, unknown>) => ReactNode
}


export interface SimState {
  query: string; scenarioId: string | null;
  running: boolean; done: boolean; awaiting: "confirm" | "clarify" | null;
  nodes: Record<NodeId, SimNodeState>;
  activeNode: NodeId | null; selectedNode: NodeId | null;
  yamlPlan: string | null; log: LogEntry[];
  result: { status: string; plan_id?: string; steps?: number } | null;
  retryCount: number;
  rawState?: Record<string, unknown>;
}
export interface LogEntry { id: number; msg: string; color: string; ts: string }

export type EdgeKind = "main" | "proceed" | "loop"
export interface EdgeDef { id: string; from: NodeId; to: NodeId; kind: EdgeKind; label?: string }

export type { ModelAuditEntry, ModelAuditRungAttempt } from "./modelAudit"
