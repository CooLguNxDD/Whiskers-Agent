import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import ConnectRevokeModal from "../ConnectRevokeModal"
import { getProviderMeta } from "../providers"

const providerMeta = getProviderMeta("whiskers_core")

describe("ConnectRevokeModal", () => {
  it("renders nothing when closed", () => {
    render(
      <ConnectRevokeModal
        open={false}
        isOauth
        basicMethod="password"
        providerMeta={providerMeta}
        pluginId="whiskers_report_plugin"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("keeps the confirm button disabled until the provider name is typed", () => {
    render(
      <ConnectRevokeModal
        open
        isOauth
        basicMethod="password"
        providerMeta={providerMeta}
        pluginId="whiskers_report_plugin"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )
    const confirmBtn = screen.getByRole("button", { name: /Revoke token/i })
    expect(confirmBtn).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText("whiskers agent"), { target: { value: "whiskers agent" } })
    expect(confirmBtn).not.toBeDisabled()
  })

  it("calls onConfirm when the typed phrase matches", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(
      <ConnectRevokeModal
        open
        isOauth
        basicMethod="password"
        providerMeta={providerMeta}
        pluginId="whiskers_report_plugin"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText("whiskers agent"), { target: { value: "whiskers agent" } })
    fireEvent.click(screen.getByRole("button", { name: /Revoke token/i }))
    await waitFor(() => expect(onConfirm).toHaveBeenCalled())
  })

  it("shows an error message when onConfirm rejects", async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error("boom"))
    render(
      <ConnectRevokeModal
        open
        isOauth
        basicMethod="password"
        providerMeta={providerMeta}
        pluginId="whiskers_report_plugin"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText("whiskers agent"), { target: { value: "whiskers agent" } })
    fireEvent.click(screen.getByRole("button", { name: /Revoke token/i }))
    await screen.findByText(/Revoke failed/i)
  })

  it("calls onCancel on Escape and on Cancel click", () => {
    const onCancel = vi.fn()
    render(
      <ConnectRevokeModal
        open
        isOauth
        basicMethod="password"
        providerMeta={providerMeta}
        pluginId="whiskers_report_plugin"
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onCancel).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(window, { key: "Escape" })
    expect(onCancel).toHaveBeenCalledTimes(2)
  })
})
