/**
 * Tests for catalogRuntime.ts — 304 snapshot preservation and the
 * refresh-once-then-throw contract for a catalog-op miss.
 *
 * See api/catalog.ts::getCatalog and api/catalogRuntime.ts::ensureCatalogClient
 * for why a 304 must never collapse the snapshot into an empty client.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import type { CatalogOperation } from "../catalog"

const sampleOp = (over: Partial<CatalogOperation> = {}): CatalogOperation => ({
  plugin_id: "api.plugins",
  operation_id: "api_list_plugins",
  description: "list",
  input_schema: { type: "object", properties: {}, additionalProperties: true },
  access: "read",
  required_scopes: [],
  visibility: "authenticated",
  version: "1",
  tags: ["plugins", "host"],
  http: { method: "GET", path_template: "/api/plugins/session_gated", auth_policy: "session_gated" },
  mcp: null,
  ui: null,
  is_fast_path: false,
  descriptor_hash: "abc",
  ...over,
})

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  })
}

describe("catalogRuntime", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("a 304 with a prior snapshot keeps operations available (no phantom empty client)", async () => {
    const runtime = await import("../catalogRuntime?t=" + Date.now())
    runtime.setCatalogSnapshot({
      revision: 1,
      etag: '"v1"',
      operations: [sampleOp()],
    })

    // Simulate a browser-triggered revalidation: server answers 304.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 304 }))

    const client = await runtime.ensureCatalogClient()
    expect(client.get("api.plugins", "api_list_plugins")?.operation_id).toBe("api_list_plugins")
    expect(runtime.getCatalogOperations()).toHaveLength(1)
  })

  it("repeated 304 with no prior snapshot throws instead of installing an empty client", async () => {
    const runtime = await import("../catalogRuntime?t=" + Date.now() + "empty")
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 304 }))

    await expect(runtime.ensureCatalogClient()).rejects.toThrow(/Catalog snapshot unavailable/)
  })

  it("callCatalogOp refreshes once and retries on a snapshot miss instead of POSTing /execute", async () => {
    const runtime = await import("../catalogRuntime?t=" + Date.now() + "miss")
    // First snapshot doesn't have the op; a refresh brings it in (e.g. after a scope change).
    runtime.setCatalogSnapshot({ revision: 1, etag: '"v1"', operations: [] })

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input)
      if (url.includes("/api/catalog/session_gated") && !url.includes("/execute")) {
        return Promise.resolve(
          jsonResponse({ revision: 2, etag: '"v2"', operations: [sampleOp()] }),
        )
      }
      if (url.includes("/api/plugins/session_gated")) {
        return Promise.resolve(jsonResponse({ plugins: [] }))
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`))
    })

    const result = await runtime.callCatalogOp("api.plugins", "api_list_plugins")
    expect(result).toEqual({ plugins: [] })

    const execCalls = fetchSpy.mock.calls.filter(([u]) => String(u).includes("/execute"))
    expect(execCalls).toHaveLength(0)
  })

  it("callCatalogOp still throws a named error when the op is unknown after a refresh", async () => {
    const runtime = await import("../catalogRuntime?t=" + Date.now() + "still-missing")
    runtime.setCatalogSnapshot({ revision: 1, etag: '"v1"', operations: [] })

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ revision: 2, etag: '"v2"', operations: [] }),
    )

    await expect(runtime.callCatalogOp("api.plugins", "api_list_plugins")).rejects.toThrow(
      /Unknown catalog operation/,
    )
  })
})
