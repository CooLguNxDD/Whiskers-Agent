import { describe, it, expect } from "vitest"
import {
  RANGE_OPTIONS,
  MCP_GRAPH_CONFIG,
  STACKED_PLUGIN_CONFIG,
  chartConfigColors,
} from "../analyticsConfig"

describe("analyticsConfig", () => {
  it("exposes the four dashboard ranges", () => {
    expect(RANGE_OPTIONS).toEqual(["1h", "24h", "7d", "30d"])
  })

  it("uses CSS theme vars, never raw oklch", () => {
    const colors = [
      ...chartConfigColors(MCP_GRAPH_CONFIG),
      ...chartConfigColors(STACKED_PLUGIN_CONFIG),
    ]
    expect(colors.length).toBeGreaterThan(0)
    for (const color of colors) {
      expect(color).toMatch(/^var\(--/)
      expect(color).not.toMatch(/oklch\(/i)
    }
  })
})
