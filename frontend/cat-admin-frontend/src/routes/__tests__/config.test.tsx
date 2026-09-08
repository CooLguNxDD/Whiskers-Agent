import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import React from "react"

const { useNavigateMock } = vi.hoisted(() => {
  return {
    useNavigateMock: vi.fn(),
  }
})

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...original,
    useNavigate: () => useNavigateMock,
  }
})

vi.mock("@/hooks/useConfig", () => {
  return {
    useGatewayConfigQuery: vi.fn().mockReturnValue({
      data: { run_graph_unified: false },
      isPending: false,
    }),
    useSaveGatewayConfigMutation: vi.fn().mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    }),
  }
})

vi.mock("@/hooks/useTotp", () => {
  return {
    useTotpStatusQuery: vi.fn(),
  }
})
import { useTotpStatusQuery } from "@/hooks/useTotp"

vi.mock("@/components/shell/AppShell", () => {
  return {
    default: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="app-shell">{children}</div>
    ),
  }
})

vi.mock("@/components/config/LlmRagSection", () => ({ default: () => <div data-testid="llm-rag-section" /> }))
vi.mock("@/components/config/GraphModelSection", () => ({ default: () => <div data-testid="graph-model-section" /> }))
vi.mock("@/components/config/StepModelSection", () => ({ default: () => <div data-testid="step-model-section" /> }))
vi.mock("@/components/config/ModelRoleSection", () => ({ default: () => <div data-testid="model-role-section" /> }))
vi.mock("@/components/config/TotpSetupSection", () => ({ default: () => <div data-testid="totp-setup-section" /> }))

import { ConfigPage } from "@/components/config/ConfigPage"

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

describe("ConfigPage Route Component", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("unprovisioned user can navigate all sections", () => {
    vi.mocked(useTotpStatusQuery).mockReturnValue({ data: { totp_provisioned: false } } as never)

    const { wrapper } = createWrapper()

    const { container } = render(<ConfigPage />, { wrapper })

    expect(screen.getByText(/Terminal access requires TOTP/i)).toBeInTheDocument()
    expect(screen.queryByText(/before you can access other settings or pages/i)).not.toBeInTheDocument()

    const anchors = container.querySelectorAll("a")
    anchors.forEach((anchor) => {
      expect(anchor.className).not.toContain("is-disabled")
      expect(anchor.textContent).not.toContain("🔒")
    })

    const serverLink = container.querySelector("nav.ct-config-side a")
    expect(serverLink).toBeInTheDocument()
    fireEvent.click(serverLink!)
    expect(useNavigateMock).toHaveBeenCalledWith({
      to: "/config",
      search: { section: "Server", returnTo: undefined },
      replace: true,
    })
  })

  it("preserves returnTo when changing sections", () => {
    vi.mocked(useTotpStatusQuery).mockReturnValue({ data: { totp_provisioned: false } } as never)
    const { wrapper } = createWrapper()
    const { container } = render(<ConfigPage returnTo="/terminal" />, { wrapper })
    const totpLink = [...container.querySelectorAll("nav.ct-config-side a")].find(
      (a) => a.textContent === "TOTP Setup",
    )
    fireEvent.click(totpLink!)
    expect(useNavigateMock).toHaveBeenCalledWith({
      to: "/config",
      search: { section: "TOTP Setup", returnTo: "/terminal" },
      replace: true,
    })
  })

  it("provisioned user sees no banner", () => {
    vi.mocked(useTotpStatusQuery).mockReturnValue({ data: { totp_provisioned: true } } as never)

    const { wrapper } = createWrapper()

    render(<ConfigPage />, { wrapper })

    expect(screen.queryByText(/Terminal access requires TOTP/i)).not.toBeInTheDocument()
  })

  it("null/loading totp state shows no banner", () => {
    vi.mocked(useTotpStatusQuery).mockReturnValue({ data: undefined } as never)

    const { wrapper } = createWrapper()

    render(<ConfigPage />, { wrapper })

    expect(screen.queryByText(/Terminal access requires TOTP/i)).not.toBeInTheDocument()
  })
})
