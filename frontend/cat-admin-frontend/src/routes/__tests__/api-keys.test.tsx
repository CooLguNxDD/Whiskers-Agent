/**
 * API Keys Route Unit Tests
 *
 * Tests the API Keys page, asserting rendering list, status badges,
 * key creation with copy-once token, and confirmation modals.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

import { ApiKeysPage } from "@/components/ApiKeys"
import {
  listApiKeys,
  createApiKey,
  type ApiKey,
} from "@/api/apiKeys"

// Mock the API module
vi.mock("@/api/apiKeys", () => {
  return {
    listApiKeys: vi.fn(),
    createApiKey: vi.fn(),
    revokeApiKey: vi.fn(),
    deleteApiKey: vi.fn(),
    updateApiKeyScopes: vi.fn(),
    listScopePresets: vi.fn().mockResolvedValue([]),
    createScopePreset: vi.fn(),
    deleteScopePreset: vi.fn(),
    getScopeVocabulary: vi.fn().mockResolvedValue({
      global_scopes: ["whiskers"],
      core_scopes: [
        { token: "core:terminal:write", description: "Write terminal", level: 1 },
        { token: "core:terminal:read", description: "Read terminal", level: 1 },
      ],
      plugin_scopes: [],
    }),
  }
})


// Mock AppShell to simplify testing and avoid router dependency
vi.mock("@/components/shell/AppShell", () => {
  return {
    default: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="app-shell">{children}</div>
    ),
  }
})

/** Create test query wrapper */
function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, wrapper }
}

describe("API Keys Route Component", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders rows from listApiKeys showing prefix and not full key, and handles revoked status badge", async () => {
    const mockKeys = [
      {
        key_id: "key-1",
        name: "Production Token",
        prefix: "whiskers_prod_",
        status: "active" as const,
        expires_at: "2026-06-25T00:00:00.000Z",
        last_used_at: "2026-06-20T00:00:00.000Z",
        created_at: "2026-06-19T00:00:00.000Z",
        revoked_at: null,
        scopes: null,
      },
      {
        key_id: "key-2",
        name: "Staging Token",
        prefix: "whiskers_stage_",
        status: "revoked" as const,
        expires_at: null,
        last_used_at: null,
        created_at: "2026-06-18T00:00:00.000Z",
        revoked_at: "2026-06-19T00:00:00.000Z",
        scopes: null,
      },
    ]

    vi.mocked(listApiKeys).mockResolvedValue(mockKeys)

    const { wrapper } = createWrapper()
    render(<ApiKeysPage />, { wrapper })

    // Verify name is rendered
    expect(await screen.findByText("Production Token")).toBeInTheDocument()
    expect(screen.getByText("Staging Token")).toBeInTheDocument()

    // Verify prefix is rendered as {prefix}…
    expect(screen.getByText("whiskers_prod_…")).toBeInTheDocument()
    expect(screen.getByText("whiskers_stage_…")).toBeInTheDocument()

    // Assert that the full key token is not present anywhere in the document
    expect(screen.queryByText(/whiskers_prod_[a-zA-Z0-9]+/)).not.toBeInTheDocument()

    // Verify status badges (accounts for both filter chips and table row badges)
    expect(screen.getAllByText("active").length).toBe(2)
    expect(screen.getAllByText("revoked").length).toBe(2)
  })

  it("creating a key renders the full token once in a copy panel with the warning", async () => {
    const mockKeys: ApiKey[] = []
    vi.mocked(listApiKeys).mockResolvedValue(mockKeys)

    const newKeyResponse = {
      key_id: "key-3",
      token: "whiskers_prod_secret_token_value_full_123",
      prefix: "whiskers_prod_",
      name: "New Integration Key",
      expires_at: null,
    }
    vi.mocked(createApiKey).mockResolvedValue(newKeyResponse)

    const { wrapper } = createWrapper()
    render(<ApiKeysPage />, { wrapper })

    // Wait for the empty state or table to be ready
    const createBtn = await screen.findByRole("button", { name: "Create API Key" })
    expect(createBtn).toBeInTheDocument()
    fireEvent.click(createBtn)

    // Fill out the key creation form
    const nameInput = screen.getByLabelText(/Name \/ Description/)
    fireEvent.change(nameInput, { target: { value: "New Integration Key" } })

    const submitButton = screen.getByRole("button", { name: "Generate Key" })
    fireEvent.click(submitButton)

    // Verify createApiKey was called with the correct parameters
    await waitFor(() => {
      expect(createApiKey).toHaveBeenCalledWith("New Integration Key", undefined)
    })

    // Assert that the full token is displayed in the copy panel
    expect(await screen.findByText(/Copy your API key/i)).toBeInTheDocument()
    expect(screen.getByText("whiskers_prod_secret_token_value_full_123")).toBeInTheDocument()

    // Assert that the warning is displayed
    expect(screen.getByText(/will not be shown again/)).toBeInTheDocument()

    // Click "Done" to close the copy panel
    const doneButton = screen.getByRole("button", { name: "Done" })
    fireEvent.click(doneButton)

    // Assert that the copy panel is closed and form is shown again
    expect(screen.queryByText(/Copy your API key/i)).not.toBeInTheDocument()
    const createBtn2 = screen.getByRole("button", { name: "Create API Key" })
    fireEvent.click(createBtn2)
    expect(screen.getByRole("button", { name: "Generate Key" })).toBeInTheDocument()
  })
})
