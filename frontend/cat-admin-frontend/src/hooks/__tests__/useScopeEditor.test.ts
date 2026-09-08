import { describe, it, expect } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useScopeEditor } from "@/hooks/useScopeEditor"
import type { Plugin } from "@/types/plugin"

const samplePlugins: Plugin[] = [
  {
    id: "acme",
    name: "Acme Plugin",
    version: "1.0.0",
    tier: "free",
    enabled: true,
    description: "An Acme plugin",
  },
]

const sampleToolsQueries = [
  {
    data: {
      tools: [
        { group: "read", access: "read" },
        { group: "write", access: "write" },
      ],
    },
    isPending: false,
  },
]

const sampleGlobalScopes = ["whiskers", "terminal:use", "terminal:host", "agy", "antigravity"]

describe("useScopeEditor Hook", () => {
  it("a. toggleScope('group:acme:read') when selectedScopes contains plugin:acme -> resulting scopes contain group:acme:read and do NOT contain plugin:acme", () => {
    const initialScopes = ["plugin:acme", "terminal:use"]
    const { result } = renderHook(() =>
      useScopeEditor(initialScopes, samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )

    act(() => {
      result.current.toggleScope("group:acme:read")
    })

    expect(result.current.selectedScopes).toContain("group:acme:read")
    expect(result.current.selectedScopes).not.toContain("plugin:acme")
    expect(result.current.selectedScopes).toContain("terminal:use")
  })

  it("b. Toggling that same group scope OFF again (now the only acme-related entry) -> resulting scopes contain neither plugin:acme nor group:acme:read", () => {
    const initialScopes = ["group:acme:read", "terminal:use"]
    const { result } = renderHook(() =>
      useScopeEditor(initialScopes, samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )

    act(() => {
      result.current.toggleScope("group:acme:read")
    })

    expect(result.current.selectedScopes).not.toContain("group:acme:read")
    expect(result.current.selectedScopes).not.toContain("plugin:acme")
    expect(result.current.selectedScopes).toContain("terminal:use")
  })

  it("c. toggleScope behavior for a NON-plugin-wide-affected scope is unchanged from current add/remove semantics", () => {
    const initialScopes = ["terminal:use"]
    const { result } = renderHook(() =>
      useScopeEditor(initialScopes, samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )

    // Toggle ON a scope without plugin-wide selected
    act(() => {
      result.current.toggleScope("group:acme:write")
    })
    expect(result.current.selectedScopes).toContain("group:acme:write")
    expect(result.current.selectedScopes).toContain("terminal:use")

    // Toggle OFF a non-plugin scope
    act(() => {
      result.current.toggleScope("terminal:use")
    })
    expect(result.current.selectedScopes).not.toContain("terminal:use")
    expect(result.current.selectedScopes).toContain("group:acme:write")
  })

  it("d. getAllAvailableScopes() no longer contains the string 'sandbox:exec'", () => {
    const { result } = renderHook(() =>
      useScopeEditor([], samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )

    const all = result.current.getAllAvailableScopes()
    expect(all).not.toContain("sandbox:exec")
    expect(all).toContain("whiskers")
    expect(all).toContain("terminal:use")
    expect(all).toContain("terminal:host")
    expect(all).toContain("agy")
    expect(all).toContain("antigravity")
  })

  it("e. getAllAvailableScopes() includes and deduplicates plugin-contributed scopes", () => {
    const samplePluginScopes = [
      { token: "agy", description: "Contributed agy", plugin_id: "test" },
      { token: "custom_scope_token", description: "Custom scope", plugin_id: "test" },
    ]
    const { result } = renderHook(() =>
      useScopeEditor([], samplePlugins, sampleToolsQueries, sampleGlobalScopes, samplePluginScopes)
    )

    const all = result.current.getAllAvailableScopes()
    expect(all).toContain("custom_scope_token")
    const agyOccurrences = all.filter((s) => s === "agy").length
    expect(agyOccurrences).toBe(1)
  })

  it("f. applyPreset('all') sets selectedScopes to ['all'] sentinel", () => {
    const { result } = renderHook(() =>
      useScopeEditor([], samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )
    act(() => {
      result.current.applyPreset("all")
    })
    expect(result.current.selectedScopes).toEqual(["all"])
    expect(result.current.scopesForPersist()).toEqual(["all"])
    expect(result.current.isAllAccess()).toBe(true)
  })

  it("g. scopesForPersist maps null selection to ['all']", () => {
    const { result } = renderHook(() =>
      useScopeEditor(null, samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )
    expect(result.current.selectedScopes).toBeNull()
    expect(result.current.scopesForPersist()).toEqual(["all"])
  })

  it("h. specific scopes persist as exact list", () => {
    const initial = ["plugin:acme", "terminal:use"]
    const { result } = renderHook(() =>
      useScopeEditor(initial, samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )
    expect(result.current.scopesForPersist()).toEqual(initial)
  })

  it("i. FSM state transitions correctly across toggles and presets", () => {
    const { result } = renderHook(() =>
      useScopeEditor(null, samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )
    expect(result.current.editorState).toBe("all")

    act(() => {
      result.current.applyPreset("none")
    })
    expect(result.current.editorState).toBe("none")
    expect(result.current.selectedScopes).toEqual([])

    act(() => {
      result.current.applyPreset("terminal")
    })
    expect(result.current.editorState).toBe("terminal")
    expect(result.current.selectedScopes).toEqual(["core:terminal:write", "core:terminal:read"])

    // Toggle terminal scope to move to core state (only core:terminal:read remaining)
    act(() => {
      result.current.toggleScope("core:terminal:write")
    })
    expect(result.current.editorState).toBe("core")
    expect(result.current.selectedScopes).toEqual(["core:terminal:read"])

    // Toggle terminal scope back to move back to terminal state
    act(() => {
      result.current.toggleScope("core:terminal:write")
    })
    expect(result.current.editorState).toBe("terminal")
    expect(result.current.selectedScopes).toEqual(["core:terminal:write", "core:terminal:read"])
  })

  it("j. getAllAvailableScopes() no longer synthesizes group:<id>:<tag> chips from live tool groups", () => {
    const { result } = renderHook(() =>
      useScopeEditor([], samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )
    const all = result.current.getAllAvailableScopes()
    expect(all).not.toContain("group:acme:read")
    expect(all).not.toContain("group:acme:write")
    expect(all).toContain("plugin:acme")
  })

  it("k. detects CORE FSM state when all scopes start with core:", () => {
    const coreOnlyScopes = ["core:config:read", "core:graph:write"]
    const { result } = renderHook(() =>
      useScopeEditor(coreOnlyScopes, samplePlugins, sampleToolsQueries, sampleGlobalScopes)
    )
    expect(result.current.editorState).toBe("core")
    expect(result.current.selectedScopes).toEqual(coreOnlyScopes)
  })

  it("l. impliedFor returns write->read closure for core and full->read/write/groups for plugins", () => {
    const samplePluginScopes = [
      { token: "group:acme:read", description: "Acme read", plugin_id: "acme" },
      { token: "group:acme:write", description: "Acme write", plugin_id: "acme" },
    ]
    const { result } = renderHook(() =>
      useScopeEditor([], samplePlugins, sampleToolsQueries, sampleGlobalScopes, samplePluginScopes)
    )

    expect(result.current.impliedFor("core:terminal:write")).toEqual(["core:terminal:read"])
    expect(result.current.impliedFor("core:terminal:read")).toEqual([])
    expect(result.current.impliedFor("plugin:acme")).toEqual([
      "plugin:acme:write",
      "plugin:acme:read",
      "group:acme:read",
      "group:acme:write",
    ])
  })
})
