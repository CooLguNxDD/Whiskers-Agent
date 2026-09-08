import { describe, it, expect } from "vitest"
import { GRAPH_NODES } from "../graphTopology.gen"
import { NODE_REGISTRY } from "../NodeRegistry"

describe("NodeRegistry completeness check", () => {
  it("asserts that every node in GRAPH_NODES has a matching-id entry in NODE_REGISTRY", () => {
    const registryIds = new Set(NODE_REGISTRY.map((node) => node.id))
    
    GRAPH_NODES.forEach((node) => {
      expect(registryIds.has(node.id)).toBe(true)
    })
  })
})
