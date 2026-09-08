import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { isOwnBackend, fetchMcpToken, getMcpClient, resetMcpClient } from "../mcpClient"
import { streamServerLogs } from "../logs"
import { setCatalogSnapshot, invalidateCatalogClient } from "../catalogRuntime"
import type { CatalogOperation } from "../catalog"

function hostOp(
  pluginId: string,
  operationId: string,
  method: string,
  path: string,
): CatalogOperation {
  return {
    plugin_id: pluginId,
    operation_id: operationId,
    description: operationId,
    input_schema: { type: "object", properties: {}, additionalProperties: true },
    access: "read",
    required_scopes: [],
    visibility: "authenticated",
    version: "1",
    tags: [],
    http: {
      method,
      path_template: path,
      auth_policy: "session_gated",
    },
    mcp: null,
    ui: null,
    is_fast_path: false,
    descriptor_hash: operationId,
  }
}

// Mock the SDK Client
const mockCallTool = vi.fn()
const mockConnect = vi.fn().mockResolvedValue(undefined)
const mockSetRequestHandler = vi.fn()

function createMockClient() {
  return {
    connect: mockConnect,
    setRequestHandler: mockSetRequestHandler,
    callTool: mockCallTool,
    close: vi.fn().mockResolvedValue(undefined),
  }
}

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => {
  function MockClient() {
    return createMockClient()
  }
  return {
    Client: vi.fn(MockClient),
  }
})

// Mock the streamable transport using a constructible function wrapped by vi.fn()
const mockTransportConstructor = vi.fn()
function MockStreamableHTTPClientTransport(url: unknown, options: unknown) {
  mockTransportConstructor(url, options)
  return {}
}

vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
  StreamableHTTPClientTransport: vi.fn(MockStreamableHTTPClientTransport),
}))

// Mock types import side
vi.mock("@modelcontextprotocol/sdk/types.js", () => ({
  ElicitRequestSchema: {},
}))

describe("Security and Robustness Fixes", () => {
  let originalFetch: typeof globalThis.fetch
  let consoleWarnSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    originalFetch = globalThis.fetch
    consoleWarnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    vi.clearAllMocks()
    resetMcpClient()
    setCatalogSnapshot({
      revision: 1,
      etag: '"t"',
      operations: [
        hostOp("api.log", "api_logs_stream", "GET", "/api/logs/session_gated/stream"),
      ],
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    resetMcpClient()
    globalThis.fetch = originalFetch
    vi.useRealTimers()
    invalidateCatalogClient()
  })

  describe("isOwnBackend & token leakage (mcpClient.ts)", () => {
    it("excludes protocol-relative URLs from being marked as own backend", () => {
      const url = new URL("https://evil.com/mcp")
      const resource = "https://our-backend.com/mcp"
      // Leading double-slash is protocol-relative, must NOT be accepted as relative path proxy
      expect(isOwnBackend(url, "//evil.com/mcp", resource)).toBe(false)
    })

    it("accepts relative paths that are not protocol-relative", () => {
      const url = new URL("https://our-backend.com/mcp")
      const resource = "https://our-backend.com/mcp"
      expect(isOwnBackend(url, "/mcp", resource)).toBe(true)
    })

    it("accepts matching resource origin", () => {
      const url = new URL("https://our-backend.com/mcp")
      const resource = "https://our-backend.com/mcp"
      expect(isOwnBackend(url, "https://our-backend.com/mcp", resource)).toBe(true)
    })

    it("connect with urlOverride of //evil.com/mcp does not attach Authorization header", async () => {
      // Mock token retrieval
      globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString()
        if (url.includes("/api/playground/session_gated/mcp-token")) {
          return {
            ok: true,
            json: async () => ({ access_token: "secret-token", resource: "https://our-backend.com/mcp" }),
          } as Response
        }
        throw new Error(`Unexpected fetch call to ${url}`)
      })

      // Try connecting with a protocol-relative evil url
      await getMcpClient("//evil.com/mcp")

      expect(mockTransportConstructor).toHaveBeenCalledTimes(1)
      const [resolvedUrl, options] = mockTransportConstructor.mock.calls[0]
      expect(resolvedUrl.host).toBe("evil.com")
      // Authorization token must NOT be present
      expect(options.requestInit.headers.Authorization).toBeUndefined()
    })

    it("handles concurrent/rapid getMcpClient calls to different URLs correctly", async () => {
      // Mock token retrieval
      globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString()
        if (url.includes("/api/playground/session_gated/mcp-token")) {
          return {
            ok: true,
            json: async () => ({ access_token: "secret-token", resource: "https://our-backend.com/mcp" }),
          } as Response
        }
        throw new Error(`Unexpected fetch call to ${url}`)
      })

      // Initiate two rapid calls to getMcpClient with different URLs
      const promiseA = getMcpClient("//evil.com/mcp-a")
      const promiseB = getMcpClient("//evil.com/mcp-b")

      // Wait for both to finish
      const clientA = await promiseA
      const clientB = await promiseB

      // ClientA is the stale client and should have been closed
      expect(clientA.close).toHaveBeenCalledTimes(1)
      // ClientB is the active client and should NOT have been closed
      expect(clientB.close).not.toHaveBeenCalled()
    })
  })

  describe("fetchMcpToken (mcpClient.ts)", () => {
    it("uses api.get and triggers session refresh on 401, succeeding on retry", async () => {
      let refreshCalled = false
      let tokenCallCount = 0

      globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString()
        if (url.includes("/api/playground/session_gated/mcp-token")) {
          tokenCallCount++
          if (tokenCallCount === 1) {
            return {
              status: 401,
              ok: false,
              statusText: "Unauthorized",
              json: async () => ({}),
            } as Response
          } else {
            return {
              status: 200,
              ok: true,
              json: async () => ({ access_token: "refreshed-token", resource: "https://our-backend.com/mcp" }),
            } as Response
          }
        }
        if (url.includes("/api/admin/public/refresh")) {
          refreshCalled = true
          return {
            status: 200,
            ok: true,
            json: async () => ({}),
          } as Response
        }
        throw new Error(`Unexpected fetch call to ${url}`)
      })

      const result = await fetchMcpToken()
      expect(refreshCalled).toBe(true)
      expect(tokenCallCount).toBe(2)
      expect(result).toEqual({ token: "refreshed-token", resource: "https://our-backend.com/mcp" })
    })
  })

  describe("streamServerLogs buffer overflow limit (logs.ts)", () => {
    it("enforces a 1MB limit on log stream buffer and calls onError after retries exhaust", async () => {
      vi.useFakeTimers()
      const largeChunk = "a".repeat(1024 * 1024 + 10)

      const mockReader = {
        read: vi.fn().mockImplementation(async () => {
          return { done: false, value: new TextEncoder().encode(largeChunk) }
        }),
        cancel: vi.fn().mockResolvedValue(undefined),
      }

      const mockStream = {
        getReader: () => mockReader,
      }

      let attempt = 0
      globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString()
        if (url.includes("/api/logs/session_gated/stream")) {
          attempt++
          if (attempt === 1) {
            return {
              ok: true,
              body: mockStream,
            } as Response
          } else {
            return {
              ok: false,
              status: 500,
              statusText: "Internal Server Error",
            } as Response
          }
        }
        throw new Error(`Unexpected fetch call to ${url}`)
      })

      let errorThrown: Error | null = null
      const handle = streamServerLogs(
        () => {},
        (err) => {
          errorThrown = err
        }
      )

      // Let the initial connection attempt and reconnect timers run
      await vi.runAllTimersAsync()

      // The onError handler should have fired with the final exhausted error (which is the 500)
      expect(errorThrown).toBeDefined()
      expect(errorThrown!.message).toContain("Log stream failed: 500")

      // The console.warn spy should show that the first attempt logged the buffer overflow error
      const overflowWarning = consoleWarnSpy.mock.calls.find(call =>
        call[1] instanceof Error && call[1].message === "Log stream buffer overflow limit exceeded"
      )
      expect(overflowWarning).toBeDefined()

      handle.close()
    })
  })
})
