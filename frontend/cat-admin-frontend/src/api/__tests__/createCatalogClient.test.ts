/**
 * Tests for createCatalogClient — bound op callers and catalog execute integration.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { createCatalogClient } from "../generated/createCatalogClient"
import { invalidateCatalogClient } from "../catalogRuntime"
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

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  })
}

describe("createCatalogClient", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    invalidateCatalogClient()
  })

  it("exposes operations in ops map keyed by plugin_id::operation_id", () => {
    const client = createCatalogClient([sampleOp()])
    expect(client.ops.get("p1::p1__echo")?.mcp?.tool_name).toBe("echo")
    expect(client.get("p1", "p1__echo")?.operation_id).toBe("p1__echo")
  })

  it("bound op caller delegates to catalog execute", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", result: { echo: "bound" } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    )
    const client = createCatalogClient([sampleOp()])
    const echo = client.op("p1", "p1__echo")
    const result = await echo({ msg: "bound" })
    expect(result).toEqual({ echo: "bound" })
    expect(fetchSpy).toHaveBeenCalled()
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(String(url)).toContain("/api/catalog/session_gated/execute")
    expect(init.method).toBe("POST")
    const body = JSON.parse(String(init.body)) as Record<string, unknown>
    expect(body).toMatchObject({
      plugin_id: "p1",
      operation_id: "p1__echo",
      args: { msg: "bound" },
    })
  })

  it("call() matches bound op caller for the same operation", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "ok", result: 42 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    )
    const client = createCatalogClient([sampleOp()])
    const direct = await client.call("p1", "p1__echo", { msg: "x" })
    const bound = await client.op("p1", "p1__echo")({ msg: "x" })
    expect(direct).toBe(bound)
  })

  it("HTTP exposure fills path params and uses api transport path", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    )
    const client = createCatalogClient([
      sampleOp({
        plugin_id: "api.plugins",
        operation_id: "api_enable_plugin",
        is_fast_path: false,
        mcp: null,
        http: {
          method: "POST",
          path_template: "/api/plugins/session_gated/{plugin_id}/enable",
          auth_policy: "session_gated",
        },
      }),
    ])
    await client.op("api.plugins", "api_enable_plugin")({ plugin_id: "demo" })
    expect(fetchSpy).toHaveBeenCalled()
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(String(url)).toBe("/api/plugins/session_gated/demo/enable")
    expect(init.method).toBe("POST")
  })

  it("call() refreshes once on a snapshot miss instead of POSTing /execute", async () => {
    const hostOp = sampleOp({
      plugin_id: "api.plugins",
      operation_id: "api_list_plugins",
      is_fast_path: false,
      mcp: null,
      http: {
        method: "GET",
        path_template: "/api/plugins/session_gated",
        auth_policy: "session_gated",
      },
    })
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input)
      if (url.includes("/api/catalog/session_gated") && !url.includes("/execute")) {
        return Promise.resolve(
          jsonResponse({ revision: 2, etag: '"v2"', operations: [hostOp] }),
        )
      }
      if (url.includes("/api/plugins/session_gated")) {
        return Promise.resolve(jsonResponse({ plugins: [] }))
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`))
    })

    const client = createCatalogClient([])
    const result = await client.call("api.plugins", "api_list_plugins")
    expect(result).toEqual({ plugins: [] })
    expect(fetchSpy.mock.calls.filter(([u]) => String(u).includes("/execute"))).toHaveLength(0)
  })

  it("op() uses the same refresh-once miss path as call()", async () => {
    const hostOp = sampleOp({
      plugin_id: "api.plugins",
      operation_id: "api_list_plugins",
      is_fast_path: false,
      mcp: null,
      http: {
        method: "GET",
        path_template: "/api/plugins/session_gated",
        auth_policy: "session_gated",
      },
    })
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input)
      if (url.includes("/api/catalog/session_gated") && !url.includes("/execute")) {
        return Promise.resolve(
          jsonResponse({ revision: 2, etag: '"v2"', operations: [hostOp] }),
        )
      }
      if (url.includes("/api/plugins/session_gated")) {
        return Promise.resolve(jsonResponse({ plugins: [] }))
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`))
    })

    const client = createCatalogClient([])
    const result = await client.op("api.plugins", "api_list_plugins")()
    expect(result).toEqual({ plugins: [] })
  })

  it("call() still throws a named error when the op is unknown after a refresh", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ revision: 2, etag: '"v2"', operations: [] }),
    )
    const client = createCatalogClient([])
    await expect(client.call("api.plugins", "api_list_plugins")).rejects.toThrow(
      /Unknown catalog operation/,
    )
  })
})
