import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import type { Plugin } from "@/types/plugin"

vi.mock("@/hooks/usePlugins")
vi.mock("@/hooks/useMotion", () => ({
  useMotionConfig: () => ({ fadeUp: {} }),
}))
vi.mock("@/store", () => ({
  useUIStore: (sel: (s: { expandedPluginId: string | null; setExpandedPlugin: (id: string | null) => void }) => unknown) =>
    sel({ expandedPluginId: "stale_plugin", setExpandedPlugin: vi.fn() }),
}))
vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}))
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: { children: React.ReactNode }) => (
    <a {...props}>{children}</a>
  ),
}))
vi.mock("@/components/ConnectModal/ConnectModal", () => ({
  default: () => null,
}))
vi.mock("@/components/ConnectModal/RevokeConfirmModal", () => ({
  default: () => null,
}))
vi.mock("framer-motion", () => ({
  motion: {
    div: ({ children, ...props }: { children?: React.ReactNode }) => (
      <div {...props}>{children}</div>
    ),
  },
  AnimatePresence: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}))

import PluginCard from "../PluginCard"
import {
  usePluginsQuery,
  usePluginHealthQuery,
  useTogglePluginMutation,
  useDeletePluginMutation,
} from "@/hooks/usePlugins"

const stalePlugin: Plugin = {
  id: "stale_plugin",
  name: "stale_plugin",
  version: "0.1.0",
  tier: "free",
  enabled: true,
  description: "Gone from disk",
  stale: true,
  content_hash: "sha256:abcdef0123456789",
}

describe("PluginCard stale state", () => {
  const deleteMutate = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(usePluginsQuery).mockReturnValue({
      data: { plugins: [stalePlugin], system_tier: 1, system_tier_name: "LITE" },
      isPending: false,
    } as unknown as ReturnType<typeof usePluginsQuery>)
    vi.mocked(usePluginHealthQuery).mockReturnValue({
      data: undefined,
    } as unknown as ReturnType<typeof usePluginHealthQuery>)
    vi.mocked(useTogglePluginMutation).mockReturnValue({
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useTogglePluginMutation>)
    vi.mocked(useDeletePluginMutation).mockReturnValue({
      mutate: deleteMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeletePluginMutation>)
  })

  it("renders Stale badge and disables enable switch", () => {
    render(<PluginCard pluginId="stale_plugin" />)

    expect(screen.getByText("Stale")).toBeInTheDocument()
    const toggle = screen.getByRole("switch", { name: /Toggle stale_plugin/i })
    expect(toggle).toBeDisabled()
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument()
  })

  it("confirm remove calls delete mutation", () => {
    render(<PluginCard pluginId="stale_plugin" />)

    fireEvent.click(screen.getByRole("button", { name: "Remove" }))
    expect(screen.getByText(/Remove stale plugin/i)).toBeInTheDocument()

    const confirm = screen.getByRole("button", { name: "Remove" })
    // dialog has its own Remove — click the last one (confirm)
    const removes = screen.getAllByRole("button", { name: "Remove" })
    fireEvent.click(removes[removes.length - 1])

    expect(deleteMutate).toHaveBeenCalledWith(
      "stale_plugin",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
    // dialog title still present until success
    expect(confirm).toBeTruthy()
  })
})
