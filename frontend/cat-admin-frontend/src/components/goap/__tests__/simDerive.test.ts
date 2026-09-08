import { describe, it, expect } from "vitest"
import { buildSimState } from "../simDerive"

describe("buildSimState active_node sync", () => {
  it("marks active_node as active when node has no output yet", () => {
    const sim = buildSimState({ user_query: "hi", active_node: "decompose" })
    expect(sim.nodes.decompose?.status).toBe("active")
    expect(sim.activeNode).toBe("decompose")
  })

  it("does not mark turn_init done before downstream signals", () => {
    const sim = buildSimState({ user_query: "hi" })
    expect(sim.nodes.turn_init?.status).toBe("idle")
  })

  it("marks turn_init done once triage_mode is present", () => {
    const sim = buildSimState({ user_query: "hi", triage_mode: "task" })
    expect(sim.nodes.turn_init?.status).toBe("done")
  })

  it("does not downgrade a done node when active_node still points at it", () => {
    const sim = buildSimState({
      user_query: "hi",
      triage_mode: "task",
      active_node: "triage",
    })
    expect(sim.nodes.triage?.status).toBe("done")
  })

  it("does not mark execution nodes done from initial-state sentinels", () => {
    const sim = buildSimState({
      user_query: "hi",
      triage_mode: "task",
      current_step_index: 0,
      step_results: [],
      caller_scopes: ["read"],
      unresolved_required: [],
    })
    expect(sim.nodes.context_check?.status).toBe("idle")
    expect(sim.nodes.step_resolver?.status).toBe("idle")
    expect(sim.nodes.permission_gate?.status).toBe("idle")
    expect(sim.nodes.executor?.status).toBe("idle")
    expect(sim.nodes.step_dispatcher?.status).toBe("idle")
  })

  it("skips task-path nodes in chat mode and does not light goap_goal", () => {
    const sim = buildSimState({
      user_query: "hello",
      triage_mode: "chat",
      response: { status: "chat", message: "Hi there" },
      goal_loop_decision: null,
      iterations: 0,
      current_step_index: 0,
      caller_scopes: ["read"],
      step_results: [],
    })
    expect(sim.nodes.goap_goal?.status).toBe("skipped")
    expect(sim.nodes.planner?.status).toBe("skipped")
    expect(sim.nodes.executor?.status).toBe("skipped")
    expect(sim.nodes.validator?.status).toBe("skipped")
  })
})