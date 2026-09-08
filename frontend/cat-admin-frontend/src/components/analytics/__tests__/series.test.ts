import { describe, it, expect } from "vitest"
import {
  bucketLabel,
  padSeries,
  sparseTickLabels,
  toMcpGraphRows,
  toStackedRows,
} from "../charts/series"

describe("padSeries", () => {
  it("returns 24 zeros when missing", () => {
    expect(padSeries(undefined)).toEqual(Array(24).fill(0))
    expect(padSeries([])).toEqual(Array(24).fill(0))
  })

  it("trims and pads to 24", () => {
    expect(padSeries([1, 2], 4)).toEqual([1, 2, 0, 0])
    expect(padSeries([1, 2, 3, 4, 5], 3)).toEqual([1, 2, 3])
  })
})

describe("bucketLabel", () => {
  it("formats 24h hours and 1h minutes", () => {
    expect(bucketLabel("24h", 3)).toBe("03:00")
    expect(bucketLabel("1h", 2)).toBe("5m")
    expect(bucketLabel("7d", 4)).toBe("B4")
  })
})

describe("toMcpGraphRows", () => {
  it("empty series still yields 24 dual-axis rows", () => {
    const rows = toMcpGraphRows(undefined, "24h")
    expect(rows).toHaveLength(24)
    expect(rows[0]).toMatchObject({ label: "00:00", mcp: 0, graph: 0 })
    expect(rows[23].label).toBe("23:00")
  })

  it("does not stack mcp onto graph", () => {
    const rows = toMcpGraphRows({ mcp: [10, 20], graph: [1, 2] }, "24h")
    expect(rows[0].mcp).toBe(10)
    expect(rows[0].graph).toBe(1)
    expect((rows[0].mcp ?? 0) + (rows[0].graph ?? 0)).toBe(11)
  })
})

describe("toStackedRows", () => {
  it("preserves per-bucket stack parts", () => {
    const rows = toStackedRows(
      { core: [1, 2], extensions: [3, 4], other: [5, 6] },
      "7d",
    )
    expect(rows[0]).toMatchObject({ label: "B0", core: 1, extensions: 3, other: 5 })
    expect(rows[1]).toMatchObject({ core: 2, extensions: 4, other: 6 })
  })
})

describe("sparseTickLabels", () => {
  it("keeps 0/6/12/18/last", () => {
    const rows = toMcpGraphRows(undefined, "24h")
    expect(sparseTickLabels(rows)).toEqual(["00:00", "06:00", "12:00", "18:00", "23:00"])
  })
})
