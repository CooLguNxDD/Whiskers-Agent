// AUTO-GENERATED from core_graph/model_roles/ — DO NOT EDIT.
// Regenerate: python scripts/gen_graph_topology.py
import type { NodeId } from "./graphTopology.gen";

export type RoleId =
  | "agent_default"
  | "builder"
  | "chat"
  | "decompose"
  | "goal_verifier"
  | "planner_goal"
  | "planner_linear"
  | "reranker"
  | "specialist"
  | "step_resolver"
  | "summary"
  | "triage";

export const EFFORT_LEVELS = ["high", "low", "max", "medium"] as const;
export const SELECTOR_ALIASES = ["core", "balanced", "fast", "strong", "strongest", "weakest"] as const;

export const CORE_ROLES: { id: RoleId; description: string }[] = [
  { id: "agent_default", description: "Default model for AgentSpec.llm_kind == 'core' (agent_loop backbone)." },
  { id: "builder", description: "YAML instruction-set compiler. Selection only." },
  { id: "chat", description: "Conversational reply node. Selection only — no validator, escalation never fires." },
  { id: "decompose", description: "Break a complex goal into sub-tasks." },
  { id: "goal_verifier", description: "GOAP goal-loop verifier (goal_check)." },
  { id: "planner_goal", description: "GOAP goal-fact extraction pass." },
  { id: "planner_linear", description: "Linear LLM planner fallback when GOAP A* finds no path." },
  { id: "reranker", description: "Candidate reranking for embedder-stage retrieval." },
  { id: "specialist", description: "Default model for the specialist stack (FlowSpec agentic stages, domain runners, generic pipeline) when no manifest/stage effort is declared." },
  { id: "step_resolver", description: "Per-step argument resolution. Selection only." },
  { id: "summary", description: "Final-turn synthesis against the never-mutated original_query." },
  { id: "triage", description: "Classify user turn as chat | classic | specialist." },
];

export const NODE_ROLES: Partial<Record<NodeId, RoleId[]>> = {
  "triage": ["triage"],
  "chat_node": ["chat"],
  "decompose": ["decompose"],
  "planner": ["planner_goal", "planner_linear"],
  "goap_goal": ["goal_verifier"],
  "summary_node": ["summary"],
  "embedder": ["reranker"],
  "specialist_entry": ["specialist"],
};
