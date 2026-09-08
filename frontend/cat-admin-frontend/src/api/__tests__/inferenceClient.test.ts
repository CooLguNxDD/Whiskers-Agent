/**
 * Path template resolution for catalog HTTP exposures.
 */

import { describe, it, expect } from "vitest"
import { resolveHttpCall } from "../inferenceClient"

describe("resolveHttpCall", () => {
  it("fills path params and leaves body for POST", () => {
    const r = resolveHttpCall(
      "/api/plugins/session_gated/{plugin_id}/skills",
      "PUT",
      { plugin_id: "p1", key: "k", content: "md" },
    )
    expect(r.path).toBe("/api/plugins/session_gated/p1/skills")
    expect(r.method).toBe("PUT")
    expect(r.body).toEqual({ key: "k", content: "md" })
  })

  it("appends leftover args as query for GET", () => {
    const r = resolveHttpCall(
      "/api/analytics/session_gated/summary",
      "GET",
      { range: "24h" },
    )
    expect(r.path).toBe("/api/analytics/session_gated/summary?range=24h")
    expect(r.body).toBeUndefined()
  })

  it("throws when path param is missing", () => {
    expect(() =>
      resolveHttpCall("/api/x/{id}", "GET", {}),
    ).toThrow(/Missing path parameter "id"/)
  })
})
