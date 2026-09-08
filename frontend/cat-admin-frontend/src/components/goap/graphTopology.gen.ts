// AUTO-GENERATED from core_graph subgraph registry (root GraphSpec) — DO NOT EDIT.
// Regenerate: python scripts/gen_graph_topology.py
export type NodeId =
  | "turn_init"
  | "triage"
  | "chat_node"
  | "decompose"
  | "embedder"
  | "planner"
  | "context_check"
  | "step_resolver"
  | "confirm_node"
  | "clarify_node"
  | "permission_gate"
  | "builder"
  | "wait_node"
  | "executor"
  | "validator"
  | "retry_node"
  | "step_dispatcher"
  | "round_summary"
  | "summary_node"
  | "goap_goal"
  | "specialist_entry";

export const GRAPH_ENTRY: NodeId = "turn_init";

export const GRAPH_NODES: { id: NodeId; router: boolean }[] = [
  { id: "turn_init", router: false },
  { id: "triage", router: true },
  { id: "chat_node", router: false },
  { id: "decompose", router: false },
  { id: "embedder", router: false },
  { id: "planner", router: true },
  { id: "context_check", router: true },
  { id: "step_resolver", router: true },
  { id: "confirm_node", router: true },
  { id: "clarify_node", router: true },
  { id: "permission_gate", router: true },
  { id: "builder", router: true },
  { id: "wait_node", router: false },
  { id: "executor", router: false },
  { id: "validator", router: true },
  { id: "retry_node", router: true },
  { id: "step_dispatcher", router: true },
  { id: "round_summary", router: false },
  { id: "summary_node", router: false },
  { id: "goap_goal", router: true },
  { id: "specialist_entry", router: true },
];

export const GRAPH_EDGES: { source: NodeId; target: NodeId; conditional: boolean }[] = [
  { source: "turn_init", target: "triage", conditional: false },
  { source: "decompose", target: "embedder", conditional: false },
  { source: "embedder", target: "planner", conditional: false },
  { source: "wait_node", target: "validator", conditional: false },
  { source: "executor", target: "validator", conditional: false },
  { source: "round_summary", target: "goap_goal", conditional: false },
  { source: "triage", target: "chat_node", conditional: true },
  { source: "triage", target: "decompose", conditional: true },
  { source: "triage", target: "specialist_entry", conditional: true },
  { source: "triage", target: "decompose", conditional: true },
  { source: "planner", target: "context_check", conditional: true },
  { source: "planner", target: "confirm_node", conditional: true },
  { source: "planner", target: "clarify_node", conditional: true },
  { source: "confirm_node", target: "context_check", conditional: true },
  { source: "clarify_node", target: "decompose", conditional: true },
  { source: "context_check", target: "permission_gate", conditional: true },
  { source: "context_check", target: "step_resolver", conditional: true },
  { source: "step_resolver", target: "permission_gate", conditional: true },
  { source: "step_resolver", target: "goap_goal", conditional: true },
  { source: "builder", target: "executor", conditional: true },
  { source: "builder", target: "wait_node", conditional: true },
  { source: "validator", target: "step_dispatcher", conditional: true },
  { source: "validator", target: "retry_node", conditional: true },
  { source: "validator", target: "decompose", conditional: true },
  { source: "validator", target: "goap_goal", conditional: true },
  { source: "retry_node", target: "permission_gate", conditional: true },
  { source: "retry_node", target: "step_resolver", conditional: true },
  { source: "step_dispatcher", target: "context_check", conditional: true },
  { source: "step_dispatcher", target: "round_summary", conditional: true },
  { source: "step_dispatcher", target: "goap_goal", conditional: true },
  { source: "permission_gate", target: "builder", conditional: true },
  { source: "goap_goal", target: "decompose", conditional: true },
  { source: "goap_goal", target: "summary_node", conditional: true },
  { source: "goap_goal", target: "triage", conditional: true },
  { source: "specialist_entry", target: "decompose", conditional: true },
  { source: "specialist_entry", target: "summary_node", conditional: true },
];
