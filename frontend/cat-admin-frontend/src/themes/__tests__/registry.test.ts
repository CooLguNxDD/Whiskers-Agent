import { describe, it, expect, vi } from "vitest"
import {
  themeRegistry,
  isValidRawTheme,
  buildRegistry,
  type RawThemeFile,
} from "../registry"

describe("Theme Registry - Real Glob/Wiring", () => {
  it('should have cozy theme with bg "oklch(0.18 0.018 45)"', () => {
    expect(themeRegistry.cozy).toBeDefined()
    expect(themeRegistry.cozy.vars.bg).toBe("oklch(0.18 0.018 45)")
  })

  it("should resolve paper theme inheritance from cozy", () => {
    expect(themeRegistry.paper).toBeDefined()
    // Overridden value
    expect(themeRegistry.paper.vars.bg).toBe("oklch(0.97 0.012 80)")
    // Inherited value
    expect(themeRegistry.paper.vars["radius"]).toBe("10px")
  })

  it("should resolve mocha from cozy and latte from mocha", () => {
    expect(themeRegistry.mocha).toBeDefined()
    expect(themeRegistry.mocha.vars.bg).toBe("oklch(0.243 0.030 284)")
    expect(themeRegistry.mocha.vars["radius"]).toBe("10px")
    expect(themeRegistry.latte).toBeDefined()
    expect(themeRegistry.latte.vars.bg).toBe("oklch(0.958 0.006 265)")
    expect(themeRegistry.latte.vars["font-sans"]).toBe(themeRegistry.cozy.vars["font-sans"])
  })

  it("computes isLight from bg OKLCH (paper light, cozy dark)", () => {
    expect(themeRegistry.paper.isLight).toBe(true)
    expect(themeRegistry.latte.isLight).toBe(true)
    expect(themeRegistry.cozy.isLight).toBe(false)
    expect(themeRegistry.mocha.isLight).toBe(false)
  })
})

describe("Theme Registry - Pure Unit Logic", () => {
  describe("isValidRawTheme", () => {
    it("should return true for valid themes", () => {
      const valid: RawThemeFile = {
        id: "test",
        label: "Test Theme",
        vars: { primary: "red" },
      }
      expect(isValidRawTheme(valid)).toBe(true)

      const withOptionals: RawThemeFile = {
        id: "test-opt",
        label: "Test Optional",
        description: "cool theme",
        default: false,
        extends: "cozy",
        vars: { bg: "blue" },
      }
      expect(isValidRawTheme(withOptionals)).toBe(true)
    })

    it("should return false for malformed themes", () => {
      // Missing vars
      expect(isValidRawTheme({ id: "t", label: "T" })).toBe(false)
      // Non-string var value
      expect(isValidRawTheme({ id: "t", label: "T", vars: { a: 123 } })).toBe(false)
      // Missing id
      expect(isValidRawTheme({ label: "T", vars: {} })).toBe(false)
      // Missing label
      expect(isValidRawTheme({ id: "t", vars: {} })).toBe(false)
      // Non-boolean default
      expect(isValidRawTheme({ id: "t", label: "T", default: "true", vars: {} })).toBe(false)
      // Non-string extends
      expect(isValidRawTheme({ id: "t", label: "T", extends: 123, vars: {} })).toBe(false)
    })
  })

  describe("buildRegistry", () => {
    it("should build registry and resolve extends, excluding invalid themes without throwing", () => {
      // Mock console.error to avoid cluttered test outputs
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})

      const mockModules: Record<string, unknown> = {
        "./valid-base.theme.json": {
          id: "base",
          label: "Base",
          vars: { bg: "black", fg: "white" },
        },
        "./valid-child.theme.json": {
          id: "child",
          label: "Child",
          extends: "base",
          vars: { fg: "green" },
        },
        "./invalid.theme.json": {
          id: "invalid",
          label: "Invalid",
          vars: { bg: 123 }, // invalid: non-string value
        },
      }

      const registry = buildRegistry(mockModules)

      // Invalid should be excluded
      expect(registry.invalid).toBeUndefined()
      expect(consoleErrorSpy).toHaveBeenCalled()

      // Base should have resolved vars
      expect(registry.base).toBeDefined()
      expect(registry.base.vars.bg).toBe("black")
      expect(registry.base.vars.fg).toBe("white")

      // Child should inherit from base and override fg
      expect(registry.child).toBeDefined()
      expect(registry.child.vars.bg).toBe("black")
      expect(registry.child.vars.fg).toBe("green")

      consoleErrorSpy.mockRestore()
    })

    it("should handle cyclic extends references without crashing", () => {
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})

      const mockModules: Record<string, unknown> = {
        "./a.theme.json": {
          id: "a",
          label: "A",
          extends: "b",
          vars: { color: "red", bg: "blue" },
        },
        "./b.theme.json": {
          id: "b",
          label: "B",
          extends: "a",
          vars: { color: "green" },
        },
      }

      const registry = buildRegistry(mockModules)

      // On cycle, registry should build successfully but resolve using only their own vars
      expect(registry.a).toBeDefined()
      expect(registry.a.vars.color).toBe("red")
      expect(registry.a.vars.bg).toBe("blue")

      expect(registry.b).toBeDefined()
      expect(registry.b.vars.color).toBe("green")

      expect(consoleErrorSpy).toHaveBeenCalled()
      consoleErrorSpy.mockRestore()
    })

    it("should handle missing extends target without crashing", () => {
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})

      const mockModules: Record<string, unknown> = {
        "./child.theme.json": {
          id: "child",
          label: "Child",
          extends: "non-existent",
          vars: { color: "blue" },
        },
      }

      const registry = buildRegistry(mockModules)

      expect(registry.child).toBeDefined()
      expect(registry.child.vars.color).toBe("blue")

      expect(consoleErrorSpy).toHaveBeenCalled()
      consoleErrorSpy.mockRestore()
    })
  })
})
