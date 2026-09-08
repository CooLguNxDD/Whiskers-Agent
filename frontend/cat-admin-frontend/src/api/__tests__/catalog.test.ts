/**
 * Tests for catalog client + inference client.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { createInferenceClient } from "../inferenceClient"
import { getCatalogOpenApi } from "../catalog"
import type { CatalogOperation } from "../catalog"

const sampleOp = (over: Partial<CatalogOperation> = {}): CatalogOperation => ({
  plugin_id: "p1",
  operation_id: "p1__echo",
  description: "echo",
  input_schema: {
    type: "object",
    properties: { msg: { type: "string", title: "Message" } },
    required: ["msg"],
  },
  access: "read",
  required_scopes: [],
  visibility: "authenticated",
  version: "1",
  tags: ["p1"],
  http: null,
  mcp: { tool_name: "echo" },
  ui: null,
  is_fast_path: true,
  descriptor_hash: "abc",
  ...over,
})

describe("createInferenceClient", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("looks up operations by plugin_id + operation_id", () => {
    const client = createInferenceClient([sampleOp()])
    expect(client.get("p1", "p1__echo")?.mcp?.tool_name).toBe("echo")
    expect(client.get("p1", "missing")).toBeUndefined()
  })

  it("falls back to catalog execute for callables without http path", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", result: { echo: "hi" } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    )
    const client = createInferenceClient([sampleOp()])
    const result = await client.call("p1", "p1__echo", { msg: "hi" })
    expect(result).toEqual({ echo: "hi" })
    expect(fetchSpy).toHaveBeenCalled()
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(String(url)).toContain("/api/catalog/session_gated/execute")
    expect(init.method).toBe("POST")
  })
})

describe("getCatalogOpenApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("fetches openapi doc from session-gated endpoint", async () => {
    const doc = {
      openapi: "3.0.3",
      info: { title: "Whiskers Agent Catalog", version: "1" },
      paths: { "/items": { get: { operationId: "p1__list_items", summary: "List" } } },
    }
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(doc), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    )
    const result = await getCatalogOpenApi()
    expect(result).toEqual(doc)
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/catalog/session_gated/openapi",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("appends plugin_id query when provided", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ openapi: "3.0.3", info: { title: "T", version: "1" }, paths: {} }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    )
    await getCatalogOpenApi("my_plugin")
    expect(String(fetchSpy.mock.calls[0]?.[0])).toContain("plugin_id=my_plugin")
  })
})
