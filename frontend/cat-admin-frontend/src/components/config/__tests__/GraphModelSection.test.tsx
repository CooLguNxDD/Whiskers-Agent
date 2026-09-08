import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import React from "react"

vi.mock("@/hooks/useConfig")

import {
  useLlmPoolQuery,
  useAddLlmPoolEntryMutation,
  useDeleteLlmPoolEntryMutation,
  useSetLlmActiveMutation,
  useToggleLlmPoolEntryActiveMutation,
  useCliAgentsQuery,
} from "@/hooks/useConfig"
import GraphModelSection from "../GraphModelSection"

describe("GraphModelSection Component", () => {
  const mockAddMutate = vi.fn()
  const mockDeleteMutate = vi.fn()
  const mockSetActiveMutate = vi.fn()
  const mockToggleActiveMutate = vi.fn()

  const mockPoolData = {
    entries: [
      {
        id: "entry-1",
        name: "My OpenAI Config",
        provider: "openai",
        model: "gpt-4o",
        kind: "chat" as const,
        is_active: true,
        has_api_key: true,
        strength: 1.0,
      },
      {
        id: "entry-2",
        name: "My Vertex Config",
        provider: "gemini-vertex",
        model: "gemini-1.5-pro-preview-0409",
        kind: "core" as const,
        is_active: true,
        has_api_key: false,
        strength: 1.5,
      },
    ],
    active: {
      chat: "entry-1",
      core: "entry-2",
      embedding: undefined,
      route: undefined,
    },
  }

  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(useLlmPoolQuery).mockReturnValue({
      data: mockPoolData,
      isPending: false,
    } as unknown as ReturnType<typeof useLlmPoolQuery>)

    vi.mocked(useAddLlmPoolEntryMutation).mockReturnValue({
      mutateAsync: mockAddMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useAddLlmPoolEntryMutation>)

    vi.mocked(useDeleteLlmPoolEntryMutation).mockReturnValue({
      mutateAsync: mockDeleteMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteLlmPoolEntryMutation>)

    vi.mocked(useSetLlmActiveMutation).mockReturnValue({
      mutateAsync: mockSetActiveMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useSetLlmActiveMutation>)

    vi.mocked(useToggleLlmPoolEntryActiveMutation).mockReturnValue({
      mutateAsync: mockToggleActiveMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useToggleLlmPoolEntryActiveMutation>)

    vi.mocked(useCliAgentsQuery).mockReturnValue({
      data: {
        drivers: [
          { name: "claude", available: true, binary: "claude" },
          { name: "agy", available: true, binary: "agy" },
          { name: "grok", available: true, binary: "grok" },
        ],
      },
      isPending: false,
      isLoading: false,
    } as unknown as ReturnType<typeof useCliAgentsQuery>)
  })

  it("renders active models and the config pool entries correctly", () => {
    render(<GraphModelSection />)

    // Check header titles
    expect(screen.getByText("Active Graph Models")).toBeInTheDocument()
    expect(screen.getByText("Model Config Pool")).toBeInTheDocument()

    // Check for the mock entries in the pool list
    expect(screen.getAllByText("My OpenAI Config").length).toBeGreaterThan(0)
    expect(screen.getAllByText("My Vertex Config").length).toBeGreaterThan(0)

    // Verify correct provider names are shown
    expect(screen.getAllByText("My Vertex Config")[0].closest("div")?.textContent).toContain("gemini-vertex")
  })

  it("has gemini-vertex in the provider dropdown and updates input helper UX", async () => {
    render(<GraphModelSection />)

    const providerSelect = screen.getByLabelText("Provider") as HTMLSelectElement
    
    // Assert options include gemini-vertex and CLI agent providers
    const options = Array.from(providerSelect.options).map((o) => o.value)
    expect(options).toContain("gemini-vertex")
    expect(options).toContain("claude-cli")
    expect(options).toContain("agy-cli")

    // Initially, provider is openai and placeholder is standard
    const apiKeyInput = screen.getByPlaceholderText("••••••••••••••••") as HTMLInputElement
    expect(apiKeyInput).toBeInTheDocument()
    expect(screen.queryByText(/Requires a Vertex AI Express API Key/)).not.toBeInTheDocument()

    // Select gemini-vertex provider
    fireEvent.change(providerSelect, { target: { value: "gemini-vertex" } })
    expect(providerSelect.value).toBe("gemini-vertex")

    // The placeholder and description text should update conditionally
    expect(screen.getByPlaceholderText("Vertex AI Express API Key")).toBeInTheDocument()
    expect(screen.getByText(/Requires a Vertex AI Express API Key \(plain string, not a GCP JSON service account key\)/)).toBeInTheDocument()
  })

  it("submits the expected payload to the backend when adding a gemini-vertex model", async () => {
    mockAddMutate.mockResolvedValue({})
    render(<GraphModelSection />)

    // Fill form
    fireEvent.change(screen.getByLabelText("Configuration Name"), {
      target: { value: "My New Vertex AI Model" },
    })

    const typeSelect = screen.getByLabelText("Model Type / Kind")
    fireEvent.change(typeSelect, { target: { value: "chat" } })

    const providerSelect = screen.getByLabelText("Provider")
    fireEvent.change(providerSelect, { target: { value: "gemini-vertex" } })

    fireEvent.change(screen.getByLabelText("Model Name"), {
      target: { value: "gemini-1.5-flash" },
    })

    fireEvent.change(screen.getByLabelText(/API Key/), {
      target: { value: "vertex_key_xyz_123" },
    })

    fireEvent.change(screen.getByLabelText(/Model Strength \/ Weight/), {
      target: { value: "1.2" },
    })

    // Submit
    const submitBtn = screen.getByRole("button", { name: "Add to pool" })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockAddMutate).toHaveBeenCalledWith({
        name: "My New Vertex AI Model",
        kind: "chat",
        provider: "gemini-vertex",
        model: "gemini-1.5-flash",
        api_key: "vertex_key_xyz_123",
        strength: 1.2,
        dimensions: undefined,
        base_url: undefined,
      })
    })

    // Inputs should clear upon success
    expect((screen.getByLabelText("Configuration Name") as HTMLInputElement).value).toBe("")
    expect((screen.getByLabelText("Model Name") as HTMLInputElement).value).toBe("")
  })

  it("submits empty model for claude-cli (never provider id as model)", async () => {
    mockAddMutate.mockResolvedValue({})
    render(<GraphModelSection />)

    fireEvent.change(screen.getByLabelText("Configuration Name"), {
      target: { value: "Local Claude CLI" },
    })
    fireEvent.change(screen.getByLabelText("Model Type / Kind"), {
      target: { value: "chat" },
    })
    fireEvent.change(screen.getByLabelText("Provider"), {
      target: { value: "claude-cli" },
    })
    // Leave model name blank — must not send "claude-cli" as model

    fireEvent.click(screen.getByRole("button", { name: "Add to pool" }))

    await waitFor(() => {
      expect(mockAddMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Local Claude CLI",
          provider: "claude-cli",
          model: "",
          kind: "chat",
        }),
      )
    })
  })

  it("submits oauth token override for claude-cli", async () => {
    mockAddMutate.mockResolvedValue({})
    render(<GraphModelSection />)

    fireEvent.change(screen.getByLabelText("Configuration Name"), {
      target: { value: "Claude CLI with OAuth" },
    })
    fireEvent.change(screen.getByLabelText("Provider"), {
      target: { value: "claude-cli" },
    })
    fireEvent.change(screen.getByLabelText(/OAuth token \/ API key override/), {
      target: { value: "setup-token-xyz" },
    })

    fireEvent.click(screen.getByRole("button", { name: "Add to pool" }))

    await waitFor(() => {
      expect(mockAddMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Claude CLI with OAuth",
          provider: "claude-cli",
          api_key: "setup-token-xyz",
        }),
      )
    })
  })
})
