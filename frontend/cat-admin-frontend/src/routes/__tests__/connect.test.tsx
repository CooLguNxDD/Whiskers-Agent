import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import React from "react"

const { useSearchMock, useNavigateMock } = vi.hoisted(() => {
  return {
    useSearchMock: vi.fn().mockReturnValue({
      oauth_success: "",
      plugin_id: "whiskers_report_plugin",
      provider: "whiskers_core",
    }),
    useNavigateMock: vi.fn(),
  }
})

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...original,
    createFileRoute: () => {
      const routeFn = (options: unknown) => ({ options, useSearch: useSearchMock })
      routeFn.useSearch = useSearchMock
      return routeFn
    },
    useNavigate: () => useNavigateMock,
    Link: ({ children, to, ...props }: React.ComponentProps<"a"> & { to?: string }) => (
      <a href={to} {...props}>{children}</a>
    ),
  }
})

const { useMcpStateMock, usePreferencesStoreMock } = vi.hoisted(() => ({
  useMcpStateMock: vi.fn().mockReturnValue(null),
  usePreferencesStoreMock: vi.fn().mockReturnValue({}),
}))

vi.mock("@/store", () => ({
  useMcpState: useMcpStateMock,
  usePreferencesStore: usePreferencesStoreMock,
  selectThemeAttrs: vi.fn(),
  useSessionStore: () => ({ mcpState: "test-state", setMcpState: vi.fn(), clearMcpState: vi.fn() }),
}))

const { usePluginsQueryMock, usePluginHealthQueryMock } = vi.hoisted(() => ({
  usePluginsQueryMock: vi.fn(),
  usePluginHealthQueryMock: vi.fn().mockReturnValue({ data: undefined }),
}))

vi.mock("@/hooks/usePlugins", () => ({
  usePluginsQuery: usePluginsQueryMock,
  usePluginHealthQuery: usePluginHealthQueryMock,
  useRevokeOAuthMutation: vi.fn().mockReturnValue({ mutateAsync: vi.fn() }),
}))

vi.mock("@/hooks/useDirectCredentials", () => ({
  useSetDirectCredentialsMutation: vi.fn().mockReturnValue({ mutate: vi.fn(), isPending: false, data: undefined, isError: false }),
  useClearDirectCredentialsMutation: vi.fn().mockReturnValue({ mutateAsync: vi.fn(), isPending: false }),
}))

import { ConnectPage } from "@/components/Connect/ConnectPage"

describe("ConnectPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMcpStateMock.mockReturnValue(null)
    usePreferencesStoreMock.mockReturnValue({})
    usePluginHealthQueryMock.mockReturnValue({ data: undefined })
    usePluginsQueryMock.mockReturnValue({ data: undefined })
  })

  it("renders the plugin <-> provider title using known provider metadata", () => {
    render(<ConnectPage />)
    expect(screen.getByText(/whiskers report/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Whiskers/).length).toBeGreaterThan(0)
  })

  it("treats a truthy oauth_success search param as connected", () => {
    useSearchMock.mockReturnValueOnce({
      oauth_success: "whiskers_report_plugin",
      plugin_id: "whiskers_report_plugin",
      provider: "whiskers_core",
    })
    render(<ConnectPage />)
    expect(screen.getByText(/authorization complete/i)).toBeInTheDocument()
  })

  it("treats health credentials_present as direct-connected on the direct tab", () => {
    usePluginsQueryMock.mockReturnValue({
      data: { plugins: [{ id: "whiskers_report_plugin", layer2_oauth_enabled: false }] },
    })
    usePluginHealthQueryMock.mockReturnValue({ data: { credentials_present: true } })
    render(<ConnectPage />)
    expect(screen.getAllByText(/credentials sealed/i).length).toBeGreaterThan(0)
  })
})
