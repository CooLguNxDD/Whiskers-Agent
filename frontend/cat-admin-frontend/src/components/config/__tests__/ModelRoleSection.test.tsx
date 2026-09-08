import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import React from "react"

vi.mock("@/hooks/useConfig")

import {
  useModelRolesQuery,
  useSaveModelRoleMutation,
  useSaveEffortMapMutation,
  useDeleteModelRoleMutation,
} from "@/hooks/useConfig"
import ModelRoleSection from "../ModelRoleSection"
import type { ModelRoleEntry, ModelRolesConfig } from "@/api/config"

const CORE_ROLE: ModelRoleEntry = {
  role_id: "triage",
  description: "Classify user turn.",
  ladder: [{ selector: "core", max_attempts: 1, timeout_s: null }],
  validate: null,
  entry_conditions: [],
  escalate_on_exception: true,
  escalate_on_invalid: true,
  terminal_fallback: "ctx_llm",
  owner: "core",
  source: "core",
  preview: [{ selector: "core", resolved_model: "claude-sonnet-5" }],
}

const DB_ROLE: ModelRoleEntry = {
  ...CORE_ROLE,
  role_id: "chat",
  owner: "db",
  source: "db",
}

const mockConfig: ModelRolesConfig = {
  roles: [CORE_ROLE, DB_ROLE],
  effort_map: { low: "fast", medium: "balanced", high: "strongest", max: "strongest" },
  pool_names: ["fast-model", "strong-model"],
  aliases: ["core", "fast", "balanced", "strong", "strongest", "weakest"],
  efforts: ["low", "medium", "high", "max"],
  node_roles: { triage: ["triage"], chat_node: ["chat"] },
}

describe("ModelRoleSection Component", () => {
  const mockSaveRole = vi.fn()
  const mockSaveEffortMap = vi.fn()
  const mockDeleteRole = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useModelRolesQuery).mockReturnValue({
      data: mockConfig,
      isPending: false,
    } as ReturnType<typeof useModelRolesQuery>)

    vi.mocked(useSaveModelRoleMutation).mockReturnValue({
      mutate: mockSaveRole,
      isError: false,
      isSuccess: false,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useSaveModelRoleMutation>)

    vi.mocked(useSaveEffortMapMutation).mockReturnValue({
      mutate: mockSaveEffortMap,
      isError: false,
      isPending: false,
    } as unknown as ReturnType<typeof useSaveEffortMapMutation>)

    vi.mocked(useDeleteModelRoleMutation).mockReturnValue({
      mutate: mockDeleteRole,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteModelRoleMutation>)
  })

  it("renders the heading and both roles with source badges", () => {
    render(<ModelRoleSection />)
    expect(screen.getByText("Model Role Ladders")).toBeInTheDocument()
    expect(screen.getByText("triage")).toBeInTheDocument()
    expect(screen.getByText("chat")).toBeInTheDocument()
    expect(screen.getAllByText("core").length).toBeGreaterThan(0)
    expect(screen.getByText("db")).toBeInTheDocument()
  })

  it("selector options include aliases, effort levels, and pool names", () => {
    render(<ModelRoleSection />)
    fireEvent.click(screen.getByText("triage"))
    const select = screen.getByLabelText("Rung 0 selector") as HTMLSelectElement
    const values = Array.from(select.options).map((o) => o.value)
    expect(values).toContain("core")
    expect(values).toContain("strongest")
    expect(values).toContain("effort:low")
    expect(values).toContain("fast-model")
  })

  it("Reset to default only appears for source === db", () => {
    render(<ModelRoleSection />)

    fireEvent.click(screen.getByText("triage"))
    expect(screen.queryByText("Reset to default")).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("chat"))
    expect(screen.getByText("Reset to default")).toBeInTheDocument()
  })

  it("Reset to default calls the delete mutation with the role id", () => {
    render(<ModelRoleSection />)
    fireEvent.click(screen.getByText("chat"))
    fireEvent.click(screen.getByText("Reset to default"))
    expect(mockDeleteRole).toHaveBeenCalledWith("chat", expect.anything())
  })

  it("Save ladder posts the current draft spec", () => {
    render(<ModelRoleSection />)
    fireEvent.click(screen.getByText("triage"))
    fireEvent.click(screen.getByText("Save ladder"))
    expect(mockSaveRole).toHaveBeenCalledWith(
      expect.objectContaining({
        roleId: "triage",
        spec: expect.objectContaining({ role_id: "triage" }),
      }),
      expect.anything(),
    )
  })

  it("changing an effort_map select posts {effort_map}", () => {
    render(<ModelRoleSection />)
    const lowSelect = screen.getByLabelText("low") as HTMLSelectElement
    fireEvent.change(lowSelect, { target: { value: "balanced" } })
    expect(mockSaveEffortMap).toHaveBeenCalledWith(
      expect.objectContaining({ low: "balanced" }),
    )
  })

  it("renders a 400 mutation error message", () => {
    vi.mocked(useSaveModelRoleMutation).mockReturnValue({
      mutate: mockSaveRole,
      isError: true,
      error: new Error("model role spec: unknown top-level keys ['bogus']"),
      isSuccess: false,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useSaveModelRoleMutation>)

    render(<ModelRoleSection />)
    fireEvent.click(screen.getByText("triage"))
    expect(screen.getByText(/unknown top-level keys/)).toBeInTheDocument()
  })
})
