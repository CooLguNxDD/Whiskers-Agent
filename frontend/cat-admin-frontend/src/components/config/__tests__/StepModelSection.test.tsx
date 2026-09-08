import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import React from "react"

vi.mock("@/hooks/useConfig")

import {
  useStepModelPolicyQuery,
  useSaveStepModelPolicyMutation,
} from "@/hooks/useConfig"
import StepModelSection from "../StepModelSection"

describe("StepModelSection Component", () => {
  const mockMutate = vi.fn()

  const mockPolicyData = {
    policy: {
      strategy: "strength" as const,
      task_type_map: {},
      op_overrides: {},
      parallel_enabled: true,
      fanout_concurrency: 5,
    },
    pool_names: ["fast", "strong"],
  }

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useStepModelPolicyQuery).mockReturnValue({
      data: mockPolicyData,
      isPending: false,
    } as ReturnType<typeof useStepModelPolicyQuery>)

    vi.mocked(useSaveStepModelPolicyMutation).mockReturnValue({
      mutate: mockMutate,
      isError: false,
      isSuccess: false,
    } as ReturnType<typeof useSaveStepModelPolicyMutation>)
  })

  it("renders the heading Step Model Routing", () => {
    render(<StepModelSection />)
    expect(screen.getByText("Step Model Routing")).toBeInTheDocument()
  })

  it("shows the current strategy value in the select", () => {
    render(<StepModelSection />)
    const strategySelect = screen.getByLabelText("Routing Strategy") as HTMLSelectElement
    expect(strategySelect.value).toBe("strength")
  })

  it("calls save mutate with task_type when strategy select changes", () => {
    render(<StepModelSection />)
    const strategySelect = screen.getByLabelText("Routing Strategy")
    fireEvent.change(strategySelect, { target: { value: "task_type" } })
    expect(mockMutate).toHaveBeenCalledWith({ strategy: "task_type" })
  })

  it("calls save mutate with parallel_enabled false when parallelism switch is toggled", () => {
    render(<StepModelSection />)
    const parallelSwitch = screen.getByRole("switch", { name: "Toggle parallel step execution" })
    fireEvent.click(parallelSwitch)
    expect(mockMutate).toHaveBeenCalledWith({ parallel_enabled: false })
  })
})