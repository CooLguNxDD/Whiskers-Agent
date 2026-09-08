import { describe, it, expect } from "vitest"
import {
  layoutGraph,
  loopArcHeight,
  COL_W,
  ROW_H,
  MAIN_STREAM,
  rowBandFor,
} from "../graphLayout"
import { GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY } from "../graphTopology.gen"

describe("layoutGraph BFS-based Layout", () => {
  it("computes the correct BFS depths on the real fixture topology", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const depths = layout.depths

    // BFS hop-count contract (unchanged — visual layers are separate)
    expect(depths["turn_init"]).toBe(0)
    expect(depths["triage"]).toBe(1)
    expect(depths["chat_node"]).toBe(2)
    expect(depths["decompose"]).toBe(2)
    expect(depths["embedder"]).toBe(3)
    expect(depths["planner"]).toBe(4)
    expect(depths["context_check"]).toBe(5)
    expect(depths["confirm_node"]).toBe(5)
    expect(depths["clarify_node"]).toBe(5)
    expect(depths["permission_gate"]).toBe(6)
    expect(depths["step_resolver"]).toBe(6)
    expect(depths["builder"]).toBe(7)
    expect(depths["goap_goal"]).toBe(7)
    expect(depths["executor"]).toBe(8)
    expect(depths["wait_node"]).toBe(8)
    expect(depths["validator"]).toBe(9)
    expect(depths["step_dispatcher"]).toBe(10)
    expect(depths["retry_node"]).toBe(10)
    expect(depths["summary_node"]).toBe(3)
  })

  it("places the main stream on an unbroken row-0 highway", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const y = (id: string) => layout.positions[id].y
    const x = (id: string) => layout.positions[id].x

    const mainY = y("turn_init")
    for (const id of MAIN_STREAM) {
      expect(layout.rows[id]).toBe(0)
      expect(y(id)).toBe(mainY)
    }

    // Sequential increasing columns along the highway
    for (let i = 1; i < MAIN_STREAM.length; i++) {
      expect(x(MAIN_STREAM[i])).toBeGreaterThan(x(MAIN_STREAM[i - 1]))
      expect(x(MAIN_STREAM[i]) - x(MAIN_STREAM[i - 1])).toBe(COL_W)
    }
  })

  it("puts secondary nodes on row 1 and sinks on row 2", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const y = (id: string) => layout.positions[id].y
    const mainY = y("builder")
    const secondaryY = mainY + ROW_H
    const sinkY = mainY + 2 * ROW_H

    // Secondary
    for (const id of ["step_resolver", "confirm_node", "wait_node", "retry_node"]) {
      expect(layout.rows[id]).toBe(1)
      expect(y(id)).toBe(secondaryY)
    }

    // Sinks
    for (const id of ["goap_goal", "clarify_node", "chat_node"]) {
      expect(layout.rows[id]).toBe(2)
      expect(y(id)).toBe(sinkY)
    }

    expect(rowBandFor("goap_goal")).toBe(2)
    expect(rowBandFor("wait_node")).toBe(1)
    expect(rowBandFor("executor")).toBe(0)
  })

  it("does not park goap_goal at BFS column 7 between builder and executor", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const x = (id: string) => layout.positions[id].x

    // Goal is an end sink: shares summary column, not builder's
    expect(layout.layers["goap_goal"]).toBe(layout.layers["summary_node"])
    expect(x("goap_goal")).toBe(x("summary_node"))
    expect(x("goap_goal")).not.toBe(x("builder"))
    expect(x("goap_goal")).toBeGreaterThan(x("executor"))
    expect(x("goap_goal")).toBeGreaterThan(x("validator"))

    // Builder → executor corridor is free of the goal node on x
    expect(layout.layers["builder"]).toBeLessThan(layout.layers["executor"])
    expect(layout.layers["goap_goal"]).toBeGreaterThan(layout.layers["executor"])
  })

  it("anchors side nodes to their main-stream columns", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const L = layout.layers

    expect(L["chat_node"]).toBe(L["decompose"])
    expect(L["confirm_node"]).toBe(L["context_check"])
    expect(L["clarify_node"]).toBe(L["context_check"])
    expect(L["step_resolver"]).toBe(L["permission_gate"])
    expect(L["wait_node"]).toBe(L["executor"])
    expect(L["retry_node"]).toBe(L["step_dispatcher"])
  })

  it("uses expanded grid dimensions for readable node spacing", () => {
    expect(COL_W).toBe(210)
    expect(ROW_H).toBe(95)

    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    expect(
      layout.positions["triage"].x - layout.positions["turn_init"].x
    ).toBe(COL_W)
    expect(
      layout.positions["step_resolver"].y - layout.positions["permission_gate"].y
    ).toBe(ROW_H)
  })

  it("classifies edge kinds from visual layers", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const findKind = (from: string, to: string) => {
      const edge = layout.edges.find(e => e.from === from && e.to === to)
      return edge ? edge.kind : undefined
    }

    // Forward highway
    expect(findKind("embedder", "planner")).toBe("main")
    expect(findKind("permission_gate", "builder")).toBe("main")

    // Same visual column
    expect(findKind("confirm_node", "context_check")).toBe("proceed")
    expect(findKind("step_resolver", "permission_gate")).toBe("proceed")
    expect(findKind("goap_goal", "summary_node")).toBe("proceed")

    // True backward loops (target layer left of source)
    expect(findKind("clarify_node", "decompose")).toBe("loop")
    expect(findKind("validator", "decompose")).toBe("loop")
    expect(findKind("step_dispatcher", "context_check")).toBe("loop")
    expect(findKind("retry_node", "permission_gate")).toBe("loop")
    expect(findKind("retry_node", "step_resolver")).toBe("loop")
    expect(findKind("goap_goal", "decompose")).toBe("loop")

    // Into end-sink goap_goal: forward or same-column, not a false BFS "loop"
    expect(findKind("validator", "goap_goal")).toBe("main")
    expect(findKind("step_dispatcher", "goap_goal")).toBe("main")
    expect(findKind("step_resolver", "goap_goal")).toBe("main")
  })

  it("produces unique staggered arc heights for loop edges", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const loops = layout.edges.filter(e => e.kind === "loop")
    expect(loops.length).toBeGreaterThan(1)

    const arcs = loops.map((e, index) => {
      const span = Math.abs(
        (layout.layers[e.from] ?? 0) - (layout.layers[e.to] ?? 0)
      )
      return loopArcHeight(span, index)
    })

    expect(new Set(arcs).size).toBe(arcs.length)
    expect(loopArcHeight(0, 0)).toBe(60)
    expect(loopArcHeight(3, 1)).toBe(60 + 60 + 18)
    expect(loopArcHeight(7, 2)).toBe(60 + 140 + 36)
  })

  it("handles cycle safety correctly in small cyclic graphs", () => {
    const nodes = [{ id: "a" }, { id: "b" }]
    const edges = [
      { source: "a", target: "b" },
      { source: "b", target: "a" },
    ]
    const layout = layoutGraph(nodes, edges, "a")

    expect(layout.depths).toEqual({ a: 0, b: 1 })
    // Small graph: BFS fallback for layers
    expect(layout.layers["a"]).toBe(0)
    expect(layout.layers["b"]).toBe(1)
  })

  it("is fully deterministic and returns identical outputs across runs", () => {
    const layout1 = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
    const layout2 = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)

    expect(layout1).toEqual(layout2)
  })

  it("adheres to positions and extents sanity bounds", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)

    expect(layout.width).toBeGreaterThan(0)
    expect(layout.height).toBeGreaterThanOrEqual(320)

    for (const node of GRAPH_NODES) {
      expect(layout.positions[node.id]).toBeDefined()
      expect(layout.positions[node.id].x).toBeGreaterThanOrEqual(0)
      expect(layout.positions[node.id].y).toBeGreaterThanOrEqual(0)
      expect(layout.layers[node.id]).toBeDefined()
      expect(layout.rows[node.id]).toBeDefined()
    }
  })

  it("includes expected cosmetic bands based on positions of key nodes", () => {
    const layout = layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)

    const planBand = layout.bands.find(b => b.label === "PLAN")
    expect(planBand).toBeDefined()
    expect(planBand?.x).toBe(layout.positions["planner"].x)

    const inputBand = layout.bands.find(b => b.label === "INPUT")
    expect(inputBand).toBeDefined()
    expect(inputBand?.x).toBe(layout.positions["turn_init"].x)

    const goalBand = layout.bands.find(b => b.label === "SUMMARY·GOAL")
    expect(goalBand).toBeDefined()
    expect(goalBand?.x).toBe(layout.positions["goap_goal"].x)
  })

  it("returns empty layout when input nodes is empty", () => {
    const layout = layoutGraph([], [])
    expect(layout).toEqual({
      positions: {},
      depths: {},
      layers: {},
      rows: {},
      edges: [],
      width: 0,
      height: 0,
      bands: [],
    })
  })
})
