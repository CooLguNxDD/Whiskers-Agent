import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render } from "@testing-library/react"
import { RootLayout } from "../__root"
import { useSessionStore } from "@/store"
import { useTotpStatusQuery } from "@/hooks/useTotp"

// Create hoisted mocks so they are initialized before imports are hoisted
const { useNavigateMock, useRouterStateMock } = vi.hoisted(() => {
  return {
    useNavigateMock: vi.fn(),
    useRouterStateMock: vi.fn(),
  }
})

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...original,
    useNavigate: () => useNavigateMock,
    useRouterState: (options?: { select?: (s: { location: { pathname: string } }) => string }) => {
      const state = {
        location: {
          pathname: useRouterStateMock(),
        },
      }
      if (options?.select) {
        return options.select(state)
      }
      return state.location.pathname
    },
    Outlet: () => <div data-testid="outlet" />,
  }
})

const { useSessionStoreMock } = vi.hoisted(() => {
  const storeState = {
    status: "idle",
    dispatch: vi.fn(),
    markAuthProbeDone: vi.fn(),
  }
  
  const hook = vi.fn((selector: (s: typeof storeState) => unknown) => selector(storeState))
  
  const setState = vi.fn((update: Partial<typeof storeState>) => {
    Object.assign(storeState, update)
  })
  
  const getState = vi.fn(() => storeState)
  
  Object.assign(hook, {
    setState,
    getState,
    storeState,
  })
  
  return {
    useSessionStoreMock: hook,
  }
})

vi.mock("@/store", () => {
  return {
    useSessionStore: useSessionStoreMock,
  }
})

vi.mock("@/hooks/useTotp", () => {
  return {
    useTotpStatusQuery: vi.fn(),
  }
})

vi.mock("@/components/RevokeModal/RevokeModal", () => {
  return {
    default: () => <div data-testid="revoke-modal" />,
  }
})

describe("RootLayout Component", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Stub fetch to resolve never so it does not trigger the auth check redirects/updates
    vi.spyOn(global, "fetch").mockImplementation(() => new Promise(() => {}))
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("redirects to TOTP Setup when status is connected, totpProvisioned is false, and on /terminal", () => {
    vi.mocked(useTotpStatusQuery).mockReturnValue({ data: { totp_provisioned: false } } as never)
    useRouterStateMock.mockReturnValue("/terminal")
    useSessionStore.setState({
      status: "connected",

    })

    render(<RootLayout />)

    expect(useNavigateMock).toHaveBeenCalledWith({
      to: "/config",
      search: { section: "TOTP Setup", returnTo: "/terminal" },
      replace: true,
    })
  })

  it("does NOT redirect when status is connected, totpProvisioned is false, but pathname is not /terminal", () => {
    const paths = ["/", "/config", "/playground", "/api-keys"]
    
    for (const path of paths) {
      vi.clearAllMocks()
      vi.mocked(useTotpStatusQuery).mockReturnValue({ data: { totp_provisioned: false } } as never)
      useRouterStateMock.mockReturnValue(path)
      useSessionStore.setState({
        status: "connected",

      })

      render(<RootLayout />)

      expect(useNavigateMock).not.toHaveBeenCalled()
    }
  })

  it("does NOT redirect when totpProvisioned is true even on /terminal", () => {
    vi.mocked(useTotpStatusQuery).mockReturnValue({ data: { totp_provisioned: true } } as never)
    useRouterStateMock.mockReturnValue("/terminal")
    useSessionStore.setState({
      status: "connected",

    })

    render(<RootLayout />)

    expect(useNavigateMock).not.toHaveBeenCalled()
  })
})
