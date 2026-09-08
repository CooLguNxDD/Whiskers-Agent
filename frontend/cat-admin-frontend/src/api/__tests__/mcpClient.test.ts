/**
 * MCP Client Unit Tests
 *
 * Specifically locks in the RequestOptions passed to callTool so that
 * server-side report_progress keepalives (during long elicitations) reset
 * the SDK request timeout and prevent -32001 Request timed out.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { runGraphMcp, resetMcpClient } from "../mcpClient"

// Mock the SDK Client
const mockCallTool = vi.fn()
const mockConnect = vi.fn().mockResolvedValue(undefined)
const mockSetRequestHandler = vi.fn()
const mockClose = vi.fn().mockResolvedValue(undefined)

function createMockClient() {
  return {
    connect: mockConnect,
    setRequestHandler: mockSetRequestHandler,
    callTool: mockCallTool,
    close: mockClose,
  }
}

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => {
  // Use a plain function (not arrow) so vitest treats it as constructor-capable
  function MockClient() {
    return createMockClient()
  }
  return {
    Client: vi.fn(MockClient),
  }
})

// Mock the streamable transport with a proper constructor function
function MockStreamableHTTPClientTransport() {
  // return value for `new ...`
  return {}
}

vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
  StreamableHTTPClientTransport: vi.fn(MockStreamableHTTPClientTransport),
}))

// Mock types import side (ElicitRequestSchema is only used in connect path)
vi.mock("@modelcontextprotocol/sdk/types.js", () => ({
  ElicitRequestSchema: {},
}))

// Mock fetch for mcp-token exchange inside connect
function mockTokenResponse(token = "test-token", resource = "") {
  return {
    ok: true,
    json: async () => ({ access_token: token, resource }),
  } as Response
}

describe("runGraphMcp RequestOptions for progress keepalive", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Ensure a fresh client per test (bypass singleton)
    // @ts-expect-error - reset internal state for test
    resetMcpClient()
    // Provide a token fetch that succeeds for own-backend
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString()
      if (url.includes("/api/playground/session_gated/mcp-token")) {
        return mockTokenResponse()
      }
      // Unexpected fetches should fail loudly in test
      throw new Error(`Unexpected fetch in mcpClient test: ${url}`)
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    // @ts-expect-error: resetMcpClient is private/internal but needed for test cleanup
    resetMcpClient()
  })

  it("passes resetTimeoutOnProgress: true together with onprogress to callTool", async () => {
    mockCallTool.mockResolvedValue({
      structuredContent: { status: "ok", steps_executed: 0 },
    })

    const result = await runGraphMcp("find record IAMANDREW and greet")

    expect(mockCallTool).toHaveBeenCalledTimes(1)

    const callArgs = mockCallTool.mock.calls[0]
    // [0] = params, [1] = resultSchema (undefined), [2] = RequestOptions
    expect(callArgs[0]).toMatchObject({
      name: "run_graph",
      arguments: { user_message: "find record IAMANDREW and greet" },
    })
    expect(callArgs[1]).toBeUndefined()

    const opts = callArgs[2]
    expect(opts).toBeDefined()
    expect(opts.timeout).toBe(600_000)
    expect(opts.resetTimeoutOnProgress).toBe(true)
    expect(typeof opts.onprogress).toBe("function")

    expect(result).toEqual({ status: "ok", steps_executed: 0 })
  })

  it("still passes session_id when provided and includes the keepalive option", async () => {
    mockCallTool.mockResolvedValue({ structuredContent: { status: "ok" } })

    await runGraphMcp("do something", {}, undefined, "sess-123")

    const callArgs = mockCallTool.mock.calls[0]
    expect(callArgs[0]).toMatchObject({
      arguments: { user_message: "do something", session_id: "sess-123" },
    })
    expect(callArgs[2].timeout).toBe(600_000)
    expect(callArgs[2].resetTimeoutOnProgress).toBe(true)
    expect(typeof callArgs[2].onprogress).toBe("function")
  })

  it("parses text content JSON into GraphResponse when structuredContent is missing", async () => {
    mockCallTool.mockResolvedValue({
      content: [{ type: "text", text: '{"status":"ok","steps_executed":2,"summary":"Found it."}' }],
    })

    const result = await runGraphMcp("fetch first session")
    expect(result).toEqual({ status: "ok", steps_executed: 2, summary: "Found it." })
  })

  it("does not put truncated JSON text into message", async () => {
    mockCallTool.mockResolvedValue({
      content: [
        {
          type: "text",
          text: '{"status":"ok","steps_executed":2,"data":[{"status":"ok","data":{"_meta":',
        },
      ],
    })

    const result = await runGraphMcp("fetch first session")
    expect(result.status).toBe("error")
    expect(result.message).toMatch(/unreadable tool response/i)
    expect(result.message).not.toContain("steps_executed")
  })

  it("keeps free-text content as message when not JSON", async () => {
    mockCallTool.mockResolvedValue({
      content: [{ type: "text", text: "plain assistant note" }],
    })

    const result = await runGraphMcp("hi")
    expect(result).toEqual({ message: "plain assistant note" })
  })
})
