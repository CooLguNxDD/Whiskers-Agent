import type { NodeDef } from "./types";
import {
  EmbedderOut, PlannerOut, GoalOut, DecomposeOut,
  TurnInitOut, TriageOut, ChatNodeOut, ContextCheckOut, ConfirmNodeOut, ClarifyNodeOut,
  StepResolverOut, PermissionGateOut, BuilderOut, WaitNodeOut, ExecutorOut, ValidatorOut,
  RetryNodeOut, StepDispatcherOut, RoundSummaryOut, SummaryNodeOut
} from "./NodeOutputs";
import { hasItems, isNonEmptyObject, planStarted } from "./deriveHelpers";

/**
 * Registry mapping node types to components.
 */
export const NODE_REGISTRY: NodeDef[] = [
  {
    id: "turn_init",
    label: "turn_init_node",
    short: "Turn Init",
    derive: (s) => {
      if (!s.user_query) return null;
      const downstream =
        s.triage_mode ||
        s.sub_tasks ||
        s.decompose_intent ||
        (Array.isArray(s.candidates) && s.candidates.length > 0);
      if (!downstream) {
        if (s.active_node === "turn_init") {
          return { status: "active", output: { query: s.user_query } };
        }
        return null;
      }
      return {
        status: "done",
        output: { query: s.user_query }
      };
    },
    render: (out) => <TurnInitOut out={out} />
  },
  {
    id: "triage",
    label: "triage_node",
    short: "Triage",
    derive: (s) => {
      if (!s.triage_mode) return null;
      return {
        status: "done",
        output: { mode: s.triage_mode }
      };
    },
    render: (out) => <TriageOut out={out} />
  },
  {
    id: "chat_node",
    label: "chat_node_node",
    short: "Chat Node",
    derive: (s) => {
      if (s.triage_mode !== "chat") return null;
      const resp = s.response as Record<string, unknown> | undefined;
      if (!resp) return null;
      return {
        status: "done",
        output: { reply: resp.message }
      };
    },
    render: (out) => <ChatNodeOut out={out} />
  },
  {
    id: "decompose",
    label: "decompose_node",
    short: "Decompose",
    derive: (s) => {
      const subTasks = s.sub_tasks as string[] | undefined;
      const intent = s.decompose_intent as string | undefined;
      if (!subTasks && !intent) return null;
      return {
        status: "done",
        output: {
          intent: intent || "",
          sub_tasks: subTasks || [],
          seed_values: s.decompose_seed_values || {}
        }
      };
    },
    render: (out) => <DecomposeOut out={out} />
  },
  {
    id: "embedder",
    label: "embedder_node",
    short: "Embedder",
    derive: (s) => {
      const c = s.candidates as Array<Record<string, unknown>>;
      if (!c || c.length === 0) return null;
      return {
        status: "done",
        output: {
          candidates: c.map((item) => ({
            route: item.operation_id,
            score: item.score,
            plugin: item.plugin_id,
          })),
          query: s.user_query,
          searched: s.searched || c.length
        }
      };
    },
    render: (out) => <EmbedderOut out={out} />
  },
  {
    id: "planner",
    label: "planner_node",
    short: "Planner",
    derive: (s) => {
      const ins = s.instruction_set;
      if (!ins || (Array.isArray(ins) && ins.length === 0)) return null;
      return {
        status: "done",
        output: {
          instruction_set: ins,
          parallel_groups: s.parallel_groups,
          workflow_name: s.workflow_name,
          plan_confidence: s.confidence,
          steps: (ins as unknown[]).length
        }
      };
    },
    render: (out) => <PlannerOut out={out} />
  },
  {
    id: "context_check",
    label: "context_check_node",
    short: "Context Check",
    derive: (s) => {
      if (!planStarted(s)) return null;
      if (!isNonEmptyObject(s.resolved_args) && !hasItems(s.unresolved_required)) return null;
      return {
        status: "done",
        output: {
          resolved_args: s.resolved_args,
          unresolved_required: s.unresolved_required
        }
      };
    },
    render: (out) => <ContextCheckOut out={out} />
  },
  {
    id: "confirm_node",
    label: "confirm_node",
    short: "Confirm Node",
    derive: (s) => {
      const resp = s.response as Record<string, unknown> | undefined;
      if (!resp || resp.status !== "confirmation_needed") return null;
      return {
        status: "waiting",
        output: { message: resp.message }
      };
    },
    render: (out) => <ConfirmNodeOut out={out} />
  },
  {
    id: "clarify_node",
    label: "clarify_node",
    short: "Clarify Node",
    derive: (s) => {
      const resp = s.response as Record<string, unknown> | undefined;
      if (!resp || resp.status !== "clarification_needed") return null;
      return {
        status: "waiting",
        output: { message: resp.message }
      };
    },
    render: (out) => <ClarifyNodeOut out={out} />
  },
  {
    id: "step_resolver",
    label: "step_resolver_node",
    short: "Step Resolver",
    derive: (s) => {
      if (!planStarted(s)) return null;
      const idx = s.current_step_index as number | undefined;
      if (!isNonEmptyObject(s.resolved_args) && (idx === undefined || idx <= 0)) return null;
      return {
        status: "done",
        output: {
          plan: s.plan,
          current_step_index: s.current_step_index
        }
      };
    },
    render: (out) => <StepResolverOut out={out} />
  },
  {
    id: "permission_gate",
    label: "permission_gate_node",
    short: "Permission Gate",
    derive: (s) => {
      if (!planStarted(s)) return null;
      if (!s.yaml_workflow && !isNonEmptyObject(s.resolved_args)) return null;
      return {
        status: "done",
        output: { scopes: s.caller_scopes }
      };
    },
    render: (out) => <PermissionGateOut out={out} />
  },
  {
    id: "builder",
    label: "builder_node",
    short: "Builder",
    derive: (s) => {
      if (!s.yaml_workflow) return null;
      return {
        status: "done",
        output: {
          yaml: s.yaml_workflow,
          plan_id: s.workflow_plan_id,
          model: s.workflow_model
        }
      };
    },
    render: (out) => <BuilderOut out={out} />
  },
  {
    id: "wait_node",
    label: "wait_node_node",
    short: "Wait Node",
    derive: (s) => {
      const step_results = s.step_results as Array<Record<string, unknown>> | undefined;
      if (!step_results) return null;
      const waitResults = step_results.filter(r => r.kind === "wait");
      if (waitResults.length === 0) return null;
      return {
        status: "done",
        output: { step_results: waitResults }
      };
    },
    render: (out) => <WaitNodeOut out={out} />
  },
  {
    id: "executor",
    label: "executor_node",
    short: "Executor",
    derive: (s) => {
      const step_results = s.step_results as Array<Record<string, unknown>> | undefined;
      if (!hasItems(step_results)) return null;
      return {
        status: "done",
        output: { step_results }
      };
    },
    render: (out) => <ExecutorOut out={out} />
  },
  {
    id: "validator",
    label: "validator_node",
    short: "Validator",
    derive: (s) => {
      if (s.goal_loop_decision) {
        return { status: "done", output: { summary: s.summary } };
      }
      const resp = s.response as Record<string, unknown> | undefined;
      const status = resp?.status as string | undefined;
      if (status === "chat" || status === "confirmation_needed" || status === "clarification_needed") {
        return null;
      }
      if (status === "ok" && hasItems(s.step_results as unknown[])) {
        return { status: "done", output: { summary: s.summary } };
      }
      return null;
    },
    render: (out) => <ValidatorOut out={out} />
  },
  {
    id: "retry_node",
    label: "retry_node",
    short: "Retry Node",
    derive: (s) => {
      if (!s.retry_count) return null;
      return {
        status: "done",
        output: { retry_count: s.retry_count }
      };
    },
    render: (out) => <RetryNodeOut out={out} />
  },
  {
    id: "step_dispatcher",
    label: "step_dispatcher_node",
    short: "Step Dispatcher",
    derive: (s) => {
      if (!planStarted(s)) return null;
      if (!hasItems(s.step_results as unknown[])) return null;
      return {
        status: "done",
        output: { current_step_index: s.current_step_index }
      };
    },
    render: (out) => <StepDispatcherOut out={out} />
  },
  {
    id: "round_summary",
    label: "round_summary_node",
    short: "Round Summary",
    derive: (s) => {
      if (!s.research_notes && !s.last_summary) return null;
      const notes = Array.isArray(s.research_notes)
        ? (s.research_notes as Array<Record<string, unknown>>)
        : [];
      const lastNote = notes.length > 0 ? notes[notes.length - 1] : null;
      return {
        status: "done",
        output: { note: lastNote || { summary: s.last_summary } }
      };
    },
    render: (out) => <RoundSummaryOut out={out} />
  },
  {
    id: "summary_node",
    label: "summary_node",
    short: "Summary Node",
    derive: (s) => {
      if (!s.summary) return null;
      return {
        status: "done",
        output: { summary: s.summary }
      };
    },
    render: (out) => <SummaryNodeOut out={out} />
  },
  {
    id: "goap_goal",
    label: "goap_goal_node",
    short: "Goap Goal",
    derive: (s) => {
      if (!s.goal) return null;
      const decision = s.goal_loop_decision;
      const iterations = (s.iterations as number) || 0;
      if ((decision === undefined || decision === null) && iterations === 0) return null;
      return {
        status: decision === "done" ? "done" : "active",
        output: {
          decision: s.goal_loop_decision,
          iterations: s.iterations,
          max_iterations: s.max_iterations,
          goal_facts: s.goal_facts,
          achieved_facts: s.achieved_facts,
          remaining_goal_facts: s.remaining_goal_facts
        }
      };
    },
    render: (out) => <GoalOut out={out} />
  },
  {
    id: "specialist_entry",
    label: "specialist_entry_node",
    short: "Specialist Entry",
    derive: (s) => {
      if (!s.specialist && s.active_node !== "specialist_entry") return null;
      return {
        status: "done",
        output: { query: (s.specialist as string) || (s.user_query as string) || "" }
      };
    },
    render: (out) => <TurnInitOut out={out} />
  }
];
