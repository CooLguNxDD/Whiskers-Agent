/**
 * ThemeProvider Test Suite
 *
 * Verifies that the ThemeProvider mounts, listens to Zustand preference store updates,
 * propagates CSS variables correctly, and throws errors when hook is used outside provider.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { render, renderHook, act, waitFor } from "@testing-library/react"
import { ThemeProvider } from "../ThemeProvider"
import { useThemeRegistry } from "@/hooks/useThemeRegistry"
import { themeRegistry } from "@/themes/registry"
import { usePreferencesStore } from "@/store"

describe("ThemeProvider", () => {
  beforeEach(() => {
    // Clear storage to avoid test state bleed in JSDOM
    if (typeof usePreferencesStore.persist?.clearStorage === "function") {
      usePreferencesStore.persist.clearStorage()
    }
    // Seed initial store state to theme "paper"
    usePreferencesStore.setState({
      theme: "paper",
    })
  })

  afterEach(() => {
    // Clean up document style attribute to prevent leaking changes between tests
    document.documentElement.removeAttribute("style")
  })

  it("applies theme variables to document.documentElement on mount", async () => {
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    )

    // Wait for the variables to be applied to the document element
    await waitFor(() => {
      const bgValue = document.documentElement.style.getPropertyValue("--bg").trim()
      expect(bgValue).toBe(themeRegistry.paper.vars.bg)
    })
  })

  it("updates theme variables when theme changes in the preferences store", async () => {
    const { rerender } = render(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    )

    // Confirm the initial paper theme variables are set
    await waitFor(() => {
      const bgValue = document.documentElement.style.getPropertyValue("--bg").trim()
      expect(bgValue).toBe(themeRegistry.paper.vars.bg)
    })

    // Update store state to neon theme
    act(() => {
      usePreferencesStore.getState().setTheme("neon")
    })

    // Re-render and wait for changes to be applied
    rerender(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    )

    await waitFor(() => {
      const bgValue = document.documentElement.style.getPropertyValue("--bg").trim()
      expect(bgValue).toBe(themeRegistry.neon.vars.bg)
    })
  })

  it("throws an error when useThemeRegistry is called outside of ThemeProvider", () => {
    expect(() => renderHook(() => useThemeRegistry())).toThrow(
      "useThemeRegistry must be used within a ThemeProvider",
    )
  })
})
