import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import React from "react"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...props }: React.ComponentProps<"a"> & { to?: string }) => (
    <a href={to} {...props}>{children}</a>
  ),
}))

const { setMutationMock, clearMutationMock, revokeMutationMock } = vi.hoisted(() => ({
  setMutationMock: { mutate: vi.fn(), isPending: false, isError: false, data: undefined as { status?: string } | undefined, error: undefined },
  clearMutationMock: { mutateAsync: vi.fn().mockResolvedValue(undefined), isPending: false },
  revokeMutationMock: { mutateAsync: vi.fn().mockResolvedValue(undefined) },
}))

vi.mock("@/hooks/useDirectCredentials", () => ({
  useSetDirectCredentialsMutation: () => setMutationMock,
  useClearDirectCredentialsMutation: () => clearMutationMock,
}))

vi.mock("@/hooks/usePlugins", () => ({
  useRevokeOAuthMutation: () => revokeMutationMock,
}))

vi.mock("@/events/bus", () => ({
  bus: { emit: vi.fn() },
}))

import { bus } from "@/events/bus"
import ConnectScreen from "../ConnectScreen"

const baseProps = {
  pluginId: "whiskers_report_plugin",
  provider: "whiskers_core",
  layer2OauthEnabled: true,
  oauthConnected: false,
  directConnected: false,
  hasReturnState: true,
  state: "pending-state-token",
  mcpState: null,
  onOauthRevoked: vi.fn(),
}

describe("ConnectScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setMutationMock.data = undefined
    setMutationMock.isPending = false
    setMutationMock.isError = false
  })

  it("defaults to the OAuth tab and lists the provider's scopes", () => {
    render(<ConnectScreen {...baseProps} />)
    expect(screen.getByRole("tab", { name: /OAuth/ })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByText("catalog:read")).toBeInTheDocument()
  })

  it("builds the authorize URL with plugin_id, state, and a sanitized provider", () => {
    render(<ConnectScreen {...baseProps} />)
    const connectLink = screen.getByRole("link", { name: /Connect to Whiskers Agent/i })
    expect(connectLink).toHaveAttribute(
      "href",
      "/oauth/plugin/whiskers_core/authorize?plugin_id=whiskers_report_plugin&state=pending-state-token",
    )
  })

  it("falls back to whiskers_core for a malicious provider value", () => {
    render(<ConnectScreen {...baseProps} provider="../../evil" />)
    const connectLink = screen.getByRole("link", { name: /Connect to/i })
    expect(connectLink.getAttribute("href")).toContain("/oauth/plugin/whiskers_core/authorize")
  })

  it("submits direct credentials with username/password on the Direct Login tab", () => {
    const { container } = render(<ConnectScreen {...baseProps} layer2OauthEnabled={false} />)
    const usernameInput = container.querySelector("#cn-user")
    const passwordInput = container.querySelector("#cn-pw")
    if (!usernameInput || !passwordInput) throw new Error("Inputs not found")
    fireEvent.change(usernameInput, { target: { value: "svc" } })
    fireEvent.change(passwordInput, { target: { value: "hunter2" } })
    fireEvent.click(screen.getByRole("button", { name: /Sign in to Whiskers Agent/i }))
    expect(setMutationMock.mutate).toHaveBeenCalledWith(
      { username: "svc", password: "hunter2" },
      expect.any(Object),
    )
  })

  it("submits an API token when the token sub-tab is active", () => {
    const { container } = render(<ConnectScreen {...baseProps} layer2OauthEnabled={false} />)
    fireEvent.click(screen.getByRole("tab", { name: /api token/i }))
    const apiTokenInput = container.querySelector("#cn-tok")
    if (!apiTokenInput) throw new Error("API token input not found")
    fireEvent.change(apiTokenInput, { target: { value: "tok_123" } })
    fireEvent.click(screen.getByRole("button", { name: /Sign in to Whiskers Agent/i }))
    expect(setMutationMock.mutate).toHaveBeenCalledWith({ api_token: "tok_123" }, expect.any(Object))
  })

  it("shows an auth_failed error row on the direct panel", () => {
    setMutationMock.data = { status: "auth_failed" }
    render(<ConnectScreen {...baseProps} layer2OauthEnabled={false} />)
    expect(screen.getByText(/Authentication failed/i)).toBeInTheDocument()
  })

  it("shows the connected banner and Disconnect CTA, and dismiss does not revoke", () => {
    render(<ConnectScreen {...baseProps} oauthConnected />)
    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Disconnect · revoke OAuth token/i })).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText("dismiss"))
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
    expect(revokeMutationMock.mutateAsync).not.toHaveBeenCalled()
  })

  it("hides the Finish link when there is no pending state", () => {
    render(<ConnectScreen {...baseProps} state="" hasReturnState={false} />)
    expect(screen.queryByText(/Finish · return to MCP client/i)).not.toBeInTheDocument()
  })

  it("disables the Finish link until connected", () => {
    render(<ConnectScreen {...baseProps} />)
    const finish = screen.getByText(/Finish · return to MCP client/i)
    expect(finish).toHaveAttribute("aria-disabled", "true")
    expect(finish).toHaveAttribute("tabIndex", "-1")
  })

  it("opens the revoke modal and calls the OAuth revoke mutation on confirm", async () => {
    render(<ConnectScreen {...baseProps} oauthConnected />)
    fireEvent.click(screen.getByRole("button", { name: /Disconnect · revoke OAuth token/i }))
    fireEvent.change(screen.getByPlaceholderText("whiskers agent"), { target: { value: "whiskers agent" } })
    fireEvent.click(screen.getByRole("button", { name: /Revoke token/i }))
    expect(revokeMutationMock.mutateAsync).toHaveBeenCalledWith("whiskers_core")
  })

  it("emits toast:error when revocation fails", async () => {
    revokeMutationMock.mutateAsync.mockRejectedValueOnce(new Error("Revoke failed"))
    render(<ConnectScreen {...baseProps} oauthConnected />)
    fireEvent.click(screen.getByRole("button", { name: /Disconnect · revoke OAuth token/i }))
    fireEvent.change(screen.getByPlaceholderText("whiskers agent"), { target: { value: "whiskers agent" } })
    fireEvent.click(screen.getByRole("button", { name: /Revoke token/i }))
    await waitFor(() => {
      expect(bus.emit).toHaveBeenCalledWith("toast:error", { message: "Revoke failed" })
    })
  })
})
