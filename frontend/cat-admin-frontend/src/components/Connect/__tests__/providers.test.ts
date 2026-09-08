import { describe, it, expect } from "vitest"
import { getProviderMeta } from "../providers"

describe("getProviderMeta", () => {
  it("returns known provider metadata for whiskers_core", () => {
    const meta = getProviderMeta("whiskers_core")
    expect(meta.name).toBe("Whiskers Agent")
    expect(meta.handle).toBe("whiskers.local")
    expect(meta.scopes).toEqual(["catalog:read", "tools:execute"])
    expect(meta.desc.length).toBeGreaterThan(0)
  })

  it("returns a generic fallback for an unknown provider id", () => {
    const meta = getProviderMeta("some_custom_provider")
    expect(meta.name).toBe("Some Custom Provider")
    expect(meta.handle).toBe("some_custom_provider")
    expect(meta.scopes).toEqual([])
    expect(meta.color).toBe("var(--amber)")
  })

  it("never returns a hardcoded hex color literal", () => {
    for (const id of ["whiskers_core", "whiskers_core", "google_sheets", "slack", "unknown_thing"]) {
      const meta = getProviderMeta(id)
      expect(meta.color).not.toMatch(/#[0-9a-fA-F]{3,8}/)
    }
  })
})
