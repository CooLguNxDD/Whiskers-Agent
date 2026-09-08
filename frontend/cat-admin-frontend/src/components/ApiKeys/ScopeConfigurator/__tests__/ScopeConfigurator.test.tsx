import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { ScopeConfigurator } from "../ScopeConfigurator"
import type { ApiKey, ScopePreset, PluginScopeContribution, CoreScopeContribution } from "@/api/apiKeys"

const mockEditingKey: ApiKey = {
  key_id: "ak_test",
  name: "Test Key",
  prefix: "octk_test_",
  status: "active",
  expires_at: null,
  last_used_at: null,
  created_at: "2026-07-03T10:00:00Z",
  revoked_at: null,
  scopes: null,
}

const mockPresets: ScopePreset[] = []

const mockPlugins = [
  { id: "my_plugin", name: "My Plugin" },
]

const mockScopeEditor = {
  selectedScopes: [] as string[] | null,
  getAllAvailableScopes: () => [],
  getPluginGroups: () => [],
  isScopeSelected: () => false,
  toggleScope: vi.fn(),
  isPluginWideSelected: () => false,
  togglePluginWide: vi.fn(),
  applyPreset: vi.fn(),
  applyCustomScopes: vi.fn(),
  scopesForPersist: () => ["all"],
  isAllAccess: () => false,
  editorState: "custom" as const,
  impliedFor: () => [],
}

describe("ScopeConfigurator Component - 3-Pane Model", () => {
  it("renders Pane 1 Core Platform with domain groups and quick presets", () => {
    const coreScopes: CoreScopeContribution[] = [
      {
        token: "core:config:read",
        description: "Read system config",
        domain: "config",
        access: "read",
        level: 1,
      },
      {
        token: "core:config:write",
        description: "Write system config",
        domain: "config",
        access: "write",
        level: 1,
      },
    ]

    render(
      <ScopeConfigurator
        editingKey={mockEditingKey}
        plugins={mockPlugins}
        expandedPlugins={{}}
        onBack={vi.fn()}
        onSave={vi.fn()}
        isSaving={false}
        scopeEditor={mockScopeEditor}
        onToggleExpand={vi.fn()}
        presets={mockPresets}
        onSaveAsPreset={vi.fn()}
        globalScopesList={["whiskers"]}
        pluginScopesList={[]}
        coreScopesList={coreScopes}
      />
    )

    // Pane 1 heading and domain tokens
    expect(screen.getByText("Level 1 — Core Platform Permissions")).toBeInTheDocument()
    expect(screen.getByText("core:config:*")).toBeInTheDocument()
    expect(screen.getByText("Quick Presets")).toBeInTheDocument()
  })

  it("navigates to Pane 2 (Plugins) and displays plugin list and contributed tokens", () => {
    const pluginScopes: PluginScopeContribution[] = [
      {
        token: "group:my_plugin:read",
        description: "Read route group",
        plugin_id: "my_plugin",
      },
    ]

    render(
      <ScopeConfigurator
        editingKey={mockEditingKey}
        plugins={mockPlugins}
        expandedPlugins={{ my_plugin: true }}
        onBack={vi.fn()}
        onSave={vi.fn()}
        isSaving={false}
        scopeEditor={mockScopeEditor}
        onToggleExpand={vi.fn()}
        presets={mockPresets}
        onSaveAsPreset={vi.fn()}
        globalScopesList={["whiskers"]}
        pluginScopesList={pluginScopes}
        coreScopesList={[]}
      />
    )

    // Click Next to navigate to Pane 2
    const nextBtn = screen.getByRole("button", { name: /Next/i })
    fireEvent.click(nextBtn)

    expect(screen.getByText("Level 2 — Plugin Scopes")).toBeInTheDocument()
    expect(screen.getByText("My Plugin")).toBeInTheDocument()
    expect(screen.getByText("group:my_plugin:read")).toBeInTheDocument()
  })

  it("navigates to Pane 3 (Review & Presets) and displays review summary", () => {
    render(
      <ScopeConfigurator
        editingKey={mockEditingKey}
        plugins={mockPlugins}
        expandedPlugins={{}}
        onBack={vi.fn()}
        onSave={vi.fn()}
        isSaving={false}
        scopeEditor={{
          ...mockScopeEditor,
          selectedScopes: ["core:terminal:write"],
          impliedFor: (t) => (t === "core:terminal:write" ? ["core:terminal:read"] : []),
        }}
        onToggleExpand={vi.fn()}
        presets={mockPresets}
        onSaveAsPreset={vi.fn()}
        globalScopesList={["whiskers"]}
        pluginScopesList={[]}
        coreScopesList={[]}
      />
    )

    // Click tab 3 (Review & Presets)
    const reviewTab = screen.getByRole("button", { name: /Review & Presets/i })
    fireEvent.click(reviewTab)

    expect(screen.getByText("Configuration Review")).toBeInTheDocument()
    expect(screen.getByText("core:terminal:write")).toBeInTheDocument()
    expect(screen.getByText(/implies: core:terminal:read/)).toBeInTheDocument()
  })
})
