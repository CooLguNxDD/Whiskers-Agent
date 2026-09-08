import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import React from "react"

vi.mock("@/hooks/useConfig")
vi.mock("@/hooks/usePlugins")
vi.mock("@/hooks/useApiKeys")

import {
  usePluginGatesQuery,
  useSavePluginGateMutation,
  useDeletePluginGateMutation,
} from "@/hooks/useConfig"
import { usePluginsQuery } from "@/hooks/usePlugins"
import { useScopeVocabularyQuery } from "@/hooks/useApiKeys"
import PluginGateSection from "../PluginGateSection"
import type { PluginGateSpec } from "@/api/config"

const MANIFEST_GATE: PluginGateSpec = {
  plugin_id: "portfolio_plugin",
  core: ["core:graph:read"],
  access: "read",
  operations: { allow: ["route_portfolio_ask"], deny: ["bake_portfolio_for_job"] },
  endpoints: ["/api/ws/*"],
  owner: "manifest",
  source: "manifest",
}

const DB_GATE: PluginGateSpec = {
  plugin_id: "cat_terminal_relay_plugin",
  core: ["core:terminal:write", "core:graph:read"],
  access: "write",
  operations: { allow: [], deny: [] },
  endpoints: [],
  owner: "db",
  source: "db",
}

describe("PluginGateSection Component", () => {
  const mockSaveGate = vi.fn()
  const mockDeleteGate = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(usePluginGatesQuery).mockReturnValue({
      data: { gates: [MANIFEST_GATE, DB_GATE] },
      isPending: false,
    } as ReturnType<typeof usePluginGatesQuery>)

    vi.mocked(usePluginsQuery).mockReturnValue({
      data: {
        plugins: [
          { id: "portfolio_plugin", name: "Portfolio Plugin", version: "1.0.0", tier: "free", enabled: true },
          { id: "cat_terminal_relay_plugin", name: "Terminal Relay", version: "1.0.0", tier: "free", enabled: true },
          { id: "ungated_plugin", name: "Ungated Plugin", version: "1.0.0", tier: "free", enabled: true },
        ],
      },
      isPending: false,
    } as ReturnType<typeof usePluginsQuery>)

    vi.mocked(useScopeVocabularyQuery).mockReturnValue({
      data: {
        global_scopes: ["whiskers"],
        core_scopes: [
          { token: "core:graph:read", description: "Read graph", level: 1 },
          { token: "core:terminal:write", description: "Write terminal", level: 1 },
        ],
      },
      isPending: false,
    } as ReturnType<typeof useScopeVocabularyQuery>)

    vi.mocked(useSavePluginGateMutation).mockReturnValue({
      mutate: mockSaveGate,
      isError: false,
      isSuccess: false,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useSavePluginGateMutation>)

    vi.mocked(useDeletePluginGateMutation).mockReturnValue({
      mutate: mockDeleteGate,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useDeletePluginGateMutation>)
  })

  it("renders the heading and all plugins with source badges including no ceiling", () => {
    render(<PluginGateSection />)
    expect(screen.getByText("Plugin Gate Ceilings")).toBeInTheDocument()
    expect(screen.getByText("portfolio_plugin")).toBeInTheDocument()
    expect(screen.getByText("cat_terminal_relay_plugin")).toBeInTheDocument()
    expect(screen.getByText("ungated_plugin")).toBeInTheDocument()
    expect(screen.getByText("manifest")).toBeInTheDocument()
    expect(screen.getByText("db")).toBeInTheDocument()
    expect(screen.getByText("no ceiling")).toBeInTheDocument()
  })

  it("Reset to manifest only appears for source === db", () => {
    render(<PluginGateSection />)

    fireEvent.click(screen.getByText("portfolio_plugin"))
    expect(screen.queryByText("Reset to manifest")).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("cat_terminal_relay_plugin"))
    expect(screen.getByText("Reset to manifest")).toBeInTheDocument()
  })

  it("Reset to manifest calls delete mutation with pluginId", () => {
    render(<PluginGateSection />)
    fireEvent.click(screen.getByText("cat_terminal_relay_plugin"))
    fireEvent.click(screen.getByText("Reset to manifest"))
    expect(mockDeleteGate).toHaveBeenCalledWith("cat_terminal_relay_plugin", expect.anything())
  })

  it("Save gate ceiling calls save mutation with spec", () => {
    render(<PluginGateSection />)
    fireEvent.click(screen.getByText("portfolio_plugin"))
    fireEvent.click(screen.getByText("Save Gate Ceiling"))
    expect(mockSaveGate).toHaveBeenCalledWith(
      expect.objectContaining({
        pluginId: "portfolio_plugin",
        spec: expect.objectContaining({
          core: ["core:graph:read"],
          access: "read",
        }),
      }),
      expect.anything()
    )
  })

  it("renders error message on save failure", () => {
    vi.mocked(useSavePluginGateMutation).mockReturnValue({
      mutate: mockSaveGate,
      isError: true,
      error: new Error("invalid_spec: core scope must start with core:"),
      isSuccess: false,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useSavePluginGateMutation>)

    render(<PluginGateSection />)
    fireEvent.click(screen.getByText("portfolio_plugin"))
    expect(screen.getByText(/core scope must start with core:/)).toBeInTheDocument()
  })
})
