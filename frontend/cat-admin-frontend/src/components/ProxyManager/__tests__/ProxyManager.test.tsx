import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, within } from "@testing-library/react"
import { ProxyManager } from "../ProxyManager"

// Mock the hooks
vi.mock("@/hooks/useProxies")

import {
  useProxiesQuery,
  useAddProxyMutation,
  useRemoveProxyMutation,
  useTestProxyMutation,
  useStartProxyOAuthMutation,
  useReindexProxyMutation,
  useUpdateProxyDescriptionMutation,
} from "@/hooks/useProxies"

describe("ProxyManager", () => {
  const mockAddMutate = vi.fn()
  const mockRemoveMutate = vi.fn()

  beforeEach(() => {
    vi.resetAllMocks()

    vi.mocked(useProxiesQuery).mockReturnValue({
      data: [
        {
          id: "1",
          name: "test-proxy",
          transport: "http",
          url: "http://localhost:5001/mcp",
          status: "active",
          authMode: "none",
          toolCount: 3,
        },
      ],
      isPending: false,
    } as unknown as ReturnType<typeof useProxiesQuery>)

    vi.mocked(useAddProxyMutation).mockReturnValue({
      mutate: mockAddMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useAddProxyMutation>)

    vi.mocked(useRemoveProxyMutation).mockReturnValue({
      mutate: mockRemoveMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useRemoveProxyMutation>)

    vi.mocked(useTestProxyMutation).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useTestProxyMutation>)

    vi.mocked(useStartProxyOAuthMutation).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useStartProxyOAuthMutation>)

    vi.mocked(useReindexProxyMutation).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useReindexProxyMutation>)

    vi.mocked(useUpdateProxyDescriptionMutation).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateProxyDescriptionMutation>)
  })

  it("renders proxies list and form", () => {
    render(<ProxyManager />)
    expect(screen.getByText("test-proxy")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. weather-service")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. http://localhost:5001/mcp")).toBeInTheDocument()
  })

  it("shows error message if name or URL is missing on submit", () => {
    render(<ProxyManager />)
    const submitBtn = screen.getByText("Add & Mount")
    fireEvent.click(submitBtn)
    expect(screen.getByText("Name and URL are required.")).toBeInTheDocument()
    expect(mockAddMutate).not.toHaveBeenCalled()
  })

  it("shows error message for invalid name characters", () => {
    render(<ProxyManager />)
    const nameInput = screen.getByPlaceholderText("e.g. weather-service")
    const urlInput = screen.getByPlaceholderText("e.g. http://localhost:5001/mcp")

    fireEvent.change(nameInput, { target: { value: "invalid name!" } })
    fireEvent.change(urlInput, { target: { value: "http://localhost:5001/mcp" } })

    const submitBtn = screen.getByText("Add & Mount")
    fireEvent.click(submitBtn)

    expect(screen.getByText("Name must contain only alphanumeric characters, dashes, and underscores.")).toBeInTheDocument()
    expect(mockAddMutate).not.toHaveBeenCalled()
  })

  it("validates URL format and shows error if invalid URL is submitted", () => {
    render(<ProxyManager />)
    const nameInput = screen.getByPlaceholderText("e.g. weather-service")
    const urlInput = screen.getByPlaceholderText("e.g. http://localhost:5001/mcp")

    fireEvent.change(nameInput, { target: { value: "valid-name" } })
    fireEvent.change(urlInput, { target: { value: "invalid-url-format" } })

    const submitBtn = screen.getByText("Add & Mount")
    fireEvent.click(submitBtn)

    expect(screen.getByText("Please enter a valid URL (e.g. http://localhost:5001/mcp).")).toBeInTheDocument()
    expect(mockAddMutate).not.toHaveBeenCalled()
  })

  it("submits successfully with valid name and URL", () => {
    render(<ProxyManager />)
    const nameInput = screen.getByPlaceholderText("e.g. weather-service")
    const urlInput = screen.getByPlaceholderText("e.g. http://localhost:5001/mcp")

    fireEvent.change(nameInput, { target: { value: "valid-name" } })
    fireEvent.change(urlInput, { target: { value: "http://localhost:5001/mcp" } })

    const submitBtn = screen.getByText("Add & Mount")
    fireEvent.click(submitBtn)

    expect(mockAddMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "valid-name",
        url: "http://localhost:5001/mcp",
      }),
      expect.any(Object)
    )
  })

  it("submits with workspaceLabel when provided", () => {
    render(<ProxyManager />)
    const nameInput = screen.getByPlaceholderText("e.g. weather-service")
    const urlInput = screen.getByPlaceholderText("e.g. http://localhost:5001/mcp")
    const wsInput = screen.getByPlaceholderText("Distinguishes proxies onto the same upstream, e.g. two Notion workspaces")

    fireEvent.change(nameInput, { target: { value: "valid-name" } })
    fireEvent.change(urlInput, { target: { value: "http://localhost:5001/mcp" } })
    fireEvent.change(wsInput, { target: { value: "prod-ws" } })

    const submitBtn = screen.getByText("Add & Mount")
    fireEvent.click(submitBtn)

    expect(mockAddMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "valid-name",
        url: "http://localhost:5001/mcp",
        workspaceLabel: "prod-ws",
      }),
      expect.any(Object)
    )
  })

  it("clicking remove shows inline confirmation instead of window.confirm", () => {
    render(<ProxyManager />)
    const removeBtn = screen.getByText("Remove")
    fireEvent.click(removeBtn)

    // Verify confirmation block is visible and has "Remove proxy test-proxy?"
    expect(screen.getByText(/Remove proxy/)).toBeInTheDocument()
    // proxy name appears in both the list and the confirm dialog code element
    expect(screen.getAllByText("test-proxy").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument()

    // Click cancel should hide it
    const cancelBtn = screen.getByRole("button", { name: "Cancel" })
    fireEvent.click(cancelBtn)
    expect(screen.queryByText(/Remove proxy/)).not.toBeInTheDocument()
  })

  it("confirming removal calls remove mutant and displays successMsg", () => {
    render(<ProxyManager />)
    const removeBtn = screen.getByText("Remove")
    fireEvent.click(removeBtn)

    // Mock mutate onSuccess to simulate server response
    mockRemoveMutate.mockImplementation((name, options) => {
      options.onSuccess({ restartRequired: true })
    })

    // Click confirm Remove
    const confirmDialog = screen.getByText(/Remove proxy/).parentElement!
    const confirmRemoveBtn = within(confirmDialog).getByRole("button", { name: "Remove" })
    fireEvent.click(confirmRemoveBtn)

    expect(mockRemoveMutate).toHaveBeenCalledWith("test-proxy", expect.any(Object))
    expect(screen.getByText(/Proxy deleted. However, a server restart is required/)).toBeInTheDocument()
  })
})
