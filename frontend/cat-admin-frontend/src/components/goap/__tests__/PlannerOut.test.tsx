import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"

import { PlannerOut } from "../NodeOutputs"

describe("PlannerOut model + parallel badges", () => {
  const baseOut = {
    plan_confidence: 0.9,
    steps: 3,
    instruction_set: [
      { step: 0, instruction: "search", op: "search_records", plugin: "core", model: "fast" },
      { step: 1, instruction: "list", op: "list_msgs", plugin: "core", model: "fast" },
      { step: 2, instruction: "summarize", op: "summarize", plugin: "core", model: "strong", deps: ["0", "1"] },
    ],
    // steps 0 and 1 are independent (parallel group), step 2 depends on both
    parallel_groups: [[0, 1], [2]],
  }

  it("renders the per-step model badge", () => {
    render(<PlannerOut out={baseOut} />)
    expect(screen.getAllByText("fast").length).toBe(2)
    expect(screen.getByText("strong")).toBeInTheDocument()
  })

  it("renders a parallel-group badge only for multi-member groups", () => {
    render(<PlannerOut out={baseOut} />)
    // steps 0 and 1 share group 1 -> two "‖ grp 1" badges; step 2 is a singleton -> none
    const badges = screen.getAllByText(/‖ grp 1/)
    expect(badges.length).toBe(2)
    expect(screen.queryByText(/‖ grp 2/)).toBeNull()
  })

  it("omits parallel badges when no multi-member groups exist", () => {
    render(<PlannerOut out={{ ...baseOut, parallel_groups: [[0], [1], [2]] }} />)
    expect(screen.queryByText(/‖ grp/)).toBeNull()
  })
})
