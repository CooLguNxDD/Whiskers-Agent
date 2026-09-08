import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import ToolsTab from "../ToolsTab"
import type { PluginTool } from "@/api/plugins"

describe("ToolsTab Component", () => {
  const mockTools: PluginTool[] = [
    {
      name: "tool_a",
      description: "Read tool under general",
      is_enabled: true,
      is_hidden: false,
      group: "general",
      access: "read",
      permission: { allow_read: true, allow_write: true, require_confirmation: false }, // auto
    },
    {
      name: "tool_b",
      description: "Write tool under records",
      is_enabled: true,
      is_hidden: true,
      group: "records",
      access: "write",
      permission: { allow_read: true, allow_write: true, require_confirmation: true }, // approval
    },
  ]

  const mockOnSetToolState = vi.fn()
  const mockOnSetToolPermission = vi.fn()
  const mockOnSetToolEmbeddingModel = vi.fn()
  const mockOnSetToolsBatch = vi.fn()

  it("renders grouped categories and tools list", () => {
    render(
      <ToolsTab
        tools={mockTools}
        isPending={false}
        ragEnabled={false}
        embeddingEntries={[]}
        onSetToolState={mockOnSetToolState}
        onSetToolPermission={mockOnSetToolPermission}
        onSetToolEmbeddingModel={mockOnSetToolEmbeddingModel}
        onSetToolsBatch={mockOnSetToolsBatch}
      />
    )

    // Verify headers
    expect(screen.getByText("general")).toBeInTheDocument()
    expect(screen.getByText("records")).toBeInTheDocument()

    // Verify tools are rendered
    expect(screen.getByText("tool_a")).toBeInTheDocument()
    expect(screen.getByText("tool_b")).toBeInTheDocument()

    expect(screen.getByText("Read tool under general")).toBeInTheDocument()
    expect(screen.getByText("Write tool under records")).toBeInTheDocument()
  })

  it("triggers callback when individual tool state is updated", () => {
    render(
      <ToolsTab
        tools={mockTools}
        isPending={false}
        ragEnabled={false}
        embeddingEntries={[]}
        onSetToolState={mockOnSetToolState}
        onSetToolPermission={mockOnSetToolPermission}
        onSetToolEmbeddingModel={mockOnSetToolEmbeddingModel}
        onSetToolsBatch={mockOnSetToolsBatch}
      />
    )

    // tool_a is currently enabled/Live. Click "Off" button under its row.
    const radios = screen.getAllByRole("radio", { name: "Off" })
    // The first "Off" is for general (group)
    // The second "Off" is for Read (subgroup)
    // The third "Off" is for tool_a
    fireEvent.click(radios[2])

    expect(mockOnSetToolState).toHaveBeenCalledWith("tool_a", "disabled")
  })

  it("triggers bulk callbacks when group state is updated", () => {
    mockOnSetToolsBatch.mockClear()
    render(
      <ToolsTab
        tools={mockTools}
        isPending={false}
        ragEnabled={false}
        embeddingEntries={[]}
        onSetToolState={mockOnSetToolState}
        onSetToolPermission={mockOnSetToolPermission}
        onSetToolEmbeddingModel={mockOnSetToolEmbeddingModel}
        onSetToolsBatch={mockOnSetToolsBatch}
      />
    )

    // Click "Off" at the "records" group level.
    const offButtons = screen.getAllByText("Off")
    // general group header: 0
    // general/read subgroup: 1
    // tool_a row: 2
    // records group header: 3
    fireEvent.click(offButtons[3])

    expect(mockOnSetToolsBatch).toHaveBeenCalledWith(["tool_b"], { state: "disabled" })
  })

  it("renders R/W/C permission buttons for individual tools and not for groups", () => {
    mockOnSetToolPermission.mockClear()
    render(
      <ToolsTab
        tools={mockTools}
        isPending={false}
        ragEnabled={false}
        embeddingEntries={[]}
        onSetToolState={mockOnSetToolState}
        onSetToolPermission={mockOnSetToolPermission}
        onSetToolEmbeddingModel={mockOnSetToolEmbeddingModel}
        onSetToolsBatch={mockOnSetToolsBatch}
      />
    )

    // tool_a and tool_b are individual tools.
    // Group level permission selectors (for category 'general' and subgroup 'read') do not have R/W/C buttons.
    // Individual tool rows (tool_a, tool_b) have R/W/C buttons.
    const rBtns = screen.getAllByTitle("Allow Read")
    const wBtns = screen.getAllByTitle("Allow Write")
    const cBtns = screen.getAllByTitle("Require Confirmation")

    // We have 2 tools, so we should see exactly 2 sets of R/W/C buttons.
    expect(rBtns.length).toBe(2)
    expect(wBtns.length).toBe(2)
    expect(cBtns.length).toBe(2)

    // Click "R" button on the first tool (tool_a) to toggle it.
    fireEvent.click(rBtns[0])
    expect(mockOnSetToolPermission).toHaveBeenCalledWith("tool_a", { allow_read: false })
  })

  it("shows Custom on group state toggle when states are mixed or permissions are custom", () => {
    render(
      <ToolsTab
        tools={mockTools}
        isPending={false}
        ragEnabled={false}
        embeddingEntries={[]}
        onSetToolState={mockOnSetToolState}
        onSetToolPermission={mockOnSetToolPermission}
        onSetToolEmbeddingModel={mockOnSetToolEmbeddingModel}
        onSetToolsBatch={mockOnSetToolsBatch}
      />
    )

    // Verify that the "Custom" segment is rendered on the group/subgroup level StateToggle.
    // The "Custom" button is only rendered for groups (where isGroup = true).
    const customSegments = screen.getAllByRole("radio", { name: "Custom" })
    expect(customSegments.length).toBeGreaterThan(0)
  })
})
