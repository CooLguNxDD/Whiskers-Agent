import { describe, it, expect, vi, beforeEach, afterEach, type MockInstance } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

// Create hoisted mocks so they are initialized before imports are hoisted
const { useSearchMock, useNavigateMock } = vi.hoisted(() => {
  return {
    useSearchMock: vi.fn().mockReturnValue({ next: "/dashboard" }),
    useNavigateMock: vi.fn(),
  }
})

vi.mock("@/store", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/store")>()
  return {
    ...original,
    useMcpState: () => "test-state",
    useSessionStore: () => ({ mcpState: "test-state", setMcpState: vi.fn(), clearMcpState: vi.fn() }),
  }
})

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...original,
    createFileRoute: () => {
      const routeFn = (options: unknown) => {
        return {
          options,
          useSearch: useSearchMock,
        }
      }
      routeFn.useSearch = useSearchMock
      return routeFn
    },
    useNavigate: () => useNavigateMock,
    Link: ({ children, to, ...props }: React.ComponentProps<"a">) => <a href={to} {...props}>{children}</a>,
  }
})

// Mock BrandMark to avoid any SVG rendering complexities
vi.mock("@/components/shell/BrandMark", () => {
  return {
    default: () => <div data-testid="brand-mark" />,
  }
})

import { LoginPage } from "@/components/login/LoginPage"

describe("Login Route Component", () => {
  let fetchSpy: MockInstance<typeof globalThis.fetch>
  let locationMock: URL & { assign: ReturnType<typeof vi.fn>; replace: ReturnType<typeof vi.fn> }

  beforeEach(() => {
    vi.clearAllMocks()

    // Stub window.location to prevent JSDOM navigation errors and allow assertions
    locationMock = new URL("http://localhost/") as unknown as URL & { assign: ReturnType<typeof vi.fn>; replace: ReturnType<typeof vi.fn> }
    locationMock.assign = vi.fn()
    locationMock.replace = vi.fn()
    let hrefValue = "http://localhost/"
    Object.defineProperty(locationMock, "href", {
      get: () => hrefValue,
      set: (val) => {
        hrefValue = val
      },
      configurable: true,
    })
    vi.spyOn(window, "location", "get").mockReturnValue(locationMock)

    // Stub global.fetch
    fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation((url: RequestInfo | URL) => {
      if (url === "/api/admin/public/exists") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ exists: true }),
        } as Response)
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ redirect: "/dashboard" }),
      } as Response)
    })
  })

  afterEach(() => {
    fetchSpy.mockRestore()
  })

  it("default mode renders username and password fields, and posts to /admin/login", async () => {
    render(<LoginPage />)

    // Wait for exists check to finish and component to render
    const usernameInput = await screen.findByLabelText("Username")
    expect(usernameInput).toBeInTheDocument()

    const passwordInput = screen.getByLabelText("Password", { selector: "input" })
    expect(passwordInput).toBeInTheDocument()
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument()

    // Fill in username and password
    fireEvent.change(usernameInput, { target: { value: "test-user" } })
    fireEvent.change(passwordInput, { target: { value: "test-password" } })

    const submitBtn = screen.getByRole("button", { name: /enter the console/i })
    expect(submitBtn).not.toBeDisabled()

    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/admin/public/login", expect.any(Object))
    })

    const fetchArgs = fetchSpy.mock.calls.find((call: unknown[]) => call[0] === "/api/admin/public/login")
    if (!fetchArgs) throw new Error("fetchArgs not found")
    const body = JSON.parse((fetchArgs[1] as RequestInit).body as string)
    expect(body).toEqual({
      username: "test-user",
      password: "test-password",
      next: "/dashboard",
      state: "test-state",
    })

    await waitFor(() => {
      expect(window.location.href).toBe("/dashboard")
    })
  })

  it("after clicking the 'Use API key' toggle, API Key input appears and posts to /admin/login-api-key", async () => {
    render(<LoginPage />)

    const usernameInput = await screen.findByLabelText("Username")
    expect(usernameInput).toBeInTheDocument()

    // Find the toggle button
    const toggleBtn = screen.getByRole("button", { name: "Use API key" })
    fireEvent.click(toggleBtn)

    // Username and Password should be gone, API Key should be present
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument()
    
    const apiKeyInput = screen.getByLabelText("API Key")
    expect(apiKeyInput).toBeInTheDocument()

    // Fill in API key
    fireEvent.change(apiKeyInput, { target: { value: "octk-example-not-a-real-key" } })

    const submitBtn = screen.getByRole("button", { name: /enter the console/i })
    expect(submitBtn).not.toBeDisabled()

    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith("/api/admin/public/login-api-key", expect.any(Object))
    })

    const fetchArgs = fetchSpy.mock.calls.find((call: unknown[]) => call[0] === "/api/admin/public/login-api-key")
    if (!fetchArgs) throw new Error("fetchArgs not found")
    const body = JSON.parse((fetchArgs[1] as RequestInit).body as string)
    expect(body).toEqual({
      api_key: "octk-example-not-a-real-key",
      next: "/dashboard",
      state: "test-state",
    })

    await waitFor(() => {
      expect(window.location.href).toBe("/dashboard")
    })
  })
})
