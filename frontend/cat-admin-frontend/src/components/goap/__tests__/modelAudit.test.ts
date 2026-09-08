import { describe, it, expect } from "vitest"
import { auditByNode, roleHintForNode, type ModelAuditEntry } from "../modelAudit"

function entry(overrides: Partial<ModelAuditEntry> = {}): ModelAuditEntry {
  return {
    role: "triage",
    node: "triage",
    rung_index: 0,
    selector: "core",
    model: "claude-sonnet-5",
    status: "ok",
    escalated: false,
    reason: null,
    attempts: [],
    elapsed_ms: 120,
    ...overrides,
  }
}

describe("auditByNode", () => {
  it("groups entries by their node field", () => {
    const rawState = {
      model_audit: [
        entry({ node: "triage", role: "triage" }),
        entry({ node: "triage", role: "triage", rung_index: 1 }),
        entry({ node: "planner", role: "planner_goal" }),
      ],
    }
    const grouped = auditByNode(rawState)
    expect(grouped.triage).toHaveLength(2)
    expect(grouped.planner).toHaveLength(1)
    expect(grouped.summary_node).toBeUndefined()
  })

  it("drops entries with a missing/unrecognized node field instead of throwing", () => {
    const rawState = {
      model_audit: [
        entry({ node: "triage" }),
        { role: "x" }, // missing node
        null,
        "garbage",
      ],
    }
    expect(() => auditByNode(rawState)).not.toThrow()
    const grouped = auditByNode(rawState)
    expect(grouped.triage).toHaveLength(1)
    expect(Object.keys(grouped)).toEqual(["triage"])
  })

  it("returns {} when model_audit is absent (flag off / legacy snapshot)", () => {
    expect(auditByNode(undefined)).toEqual({})
    expect(auditByNode({})).toEqual({})
    expect(auditByNode({ some_other_field: 1 })).toEqual({})
  })

  it("returns {} when model_audit is present but not an array", () => {
    expect(auditByNode({ model_audit: "not-an-array" })).toEqual({})
  })
})

describe("roleHintForNode", () => {
  it("returns the declared role(s) for a known node", () => {
    expect(roleHintForNode("triage")).toEqual(["triage"])
    expect(roleHintForNode("planner")).toEqual(["planner_goal", "planner_linear"])
  })

  it("returns an empty array for an unmapped node", () => {
    expect(roleHintForNode("turn_init")).toEqual([])
  })
})
