/**
 * Playground API SSE streaming unit tests.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { streamPlaygroundGoap, type GoapStreamEvent } from "../playground"
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

describe("streamPlaygroundGoap SSE streaming", () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    setCatalogSnapshot({
      revision: 1,
      etag: '"t"',
      operations: [
        hostOp("api.playground", "api_playground_stream_goap", "POST", "/api/playground/session_gated/stream_goap"),
      ],
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    invalidateCatalogClient()
  })

  it("reassembles events split across multiple read chunks", async () => {
    const chunk1 = "data: {\"type\": \"token\", \"node\": \"chat_node\", \"t"
    const chunk2 = "ext\": \"Hello\"}\n\n"

    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(chunk1) })
        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(chunk2) })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    }

    const mockStream = {
      getReader: () => mockReader,
    }

    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      body: mockStream,
    } as unknown as Response)

    const events: GoapStreamEvent[] = []
    await streamPlaygroundGoap("test query", false, (e) => events.push(e))

    expect(events).toEqual([{ type: "token", node: "chat_node", text: "Hello" }])
    expect(mockReader.read).toHaveBeenCalledTimes(3)
  })

  it("does not throw on malformed JSON; logs and emits an error event", async () => {
    const chunk = "data: {invalid json}\n\n"

    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(chunk) })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    }

    const mockStream = {
      getReader: () => mockReader,
    }

    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      body: mockStream,
    } as unknown as Response)

    const events: GoapStreamEvent[] = []
    await streamPlaygroundGoap("test query", false, (e) => events.push(e))

    expect(events).toEqual([{ type: "error", message: "Malformed SSE chunk from server" }])
    expect(consoleErrorSpy).toHaveBeenCalled()
  })

  it("drains a residual chunk left in the buffer when the stream ends without a trailing blank line", async () => {
    // No trailing "\n\n" — the stream just ends mid-chunk (abrupt abort / no final `done` frame).
    const chunk = 'data: {"type": "end"}'

    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(chunk) })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    }

    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      body: { getReader: () => mockReader },
    } as unknown as Response)

    const events: GoapStreamEvent[] = []
    await streamPlaygroundGoap("test query", false, (e) => events.push(e))

    expect(events).toEqual([{ type: "end" }])
  })

  it("parses data containing escaped newlines intact", async () => {
    const chunk = "data: {\"type\": \"token\", \"node\": \"chat_node\", \"text\": \"Hello\\n\\nWorld\"}\n\n"

    const mockReader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(chunk) })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    }

    const mockStream = {
      getReader: () => mockReader,
    }

    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      body: mockStream,
    } as unknown as Response)

    const events: GoapStreamEvent[] = []
    await streamPlaygroundGoap("test query", false, (e) => events.push(e))

    expect(events).toEqual([{ type: "token", node: "chat_node", text: "Hello\n\nWorld" }])
  })
})
