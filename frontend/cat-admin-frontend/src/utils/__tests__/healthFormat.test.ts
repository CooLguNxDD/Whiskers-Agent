import { describe, it, expect } from "vitest"
import {
  formatSuccessRate,
  formatP50,
  formatSessionAge,
  formatIdentity,
  shortHost,
  formatHealthStatus,
} from "../healthFormat"

describe("healthFormat", () => {
  it("formatSuccessRate", () => {
    expect(formatSuccessRate(100)).toBe("100%")
    expect(formatSuccessRate(99.97)).toBe("99.97%")
    expect(formatSuccessRate(99.9)).toBe("99.9%")
    expect(formatSuccessRate(0)).toBe("0%")
    expect(formatSuccessRate(null)).toBe("—")
  })

  it("formatP50", () => {
    expect(formatP50(47)).toBe("47ms")
    expect(formatP50(47.6)).toBe("48ms")
    expect(formatP50(null)).toBe("—")
  })

  it("formatSessionAge", () => {
    const now = 1_700_000_000_000 // ms
    expect(formatSessionAge(1_700_000_000 - 14 * 60, now)).toBe("14m")
    expect(formatSessionAge(1_700_000_000 - 2 * 3600, now)).toBe("2h")
    expect(formatSessionAge(1_700_000_000 - 3 * 86400, now)).toBe("3d")
    expect(formatSessionAge(1_700_000_000 - 30, now)).toBe("30s")
    expect(formatSessionAge(null, now)).toBe("—")
  })

  it("formatIdentity / shortHost", () => {
    expect(shortHost("localhost")).toBe("local")
    expect(shortHost("node_01.prod")).toBe("node_01")
    expect(formatIdentity("admin", "localhost")).toBe("admin@local")
    expect(formatIdentity("admin", "node_01.prod")).toBe("admin@node_01")
    expect(formatIdentity(null, "host")).toBe("user@host")
  })

  it("formatHealthStatus", () => {
    expect(formatHealthStatus("healthy")).toBe("healthy")
    expect(formatHealthStatus("DEGRADED")).toBe("degraded")
    expect(formatHealthStatus(null)).toBe("unknown")
  })
})
