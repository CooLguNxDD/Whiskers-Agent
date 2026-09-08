import { describe, it, expect } from "vitest"
import { createCatalogClient } from "@/api/generated/createCatalogClient"
import type { CatalogOperation } from "@/api/catalog"
import { resolveCatalogOp } from "../resolveCatalogOp"

function op(partial: Partial<CatalogOperation> & Pick<CatalogOperation, "plugin_id" | "operation_id">): CatalogOperation {
  return {
    description: "",
    input_schema: { type: "object" },
    access: "read",
    required_scopes: [],
    visibility: "authenticated",
    version: "1",
    tags: [],
    http: null,
    mcp: null,
    ui: null,
    is_fast_path: true,
    descriptor_hash: "t",
    ...partial,
  }
}

describe("resolveCatalogOp", () => {
  it("matches exact plugin + operation_id", () => {
    const client = createCatalogClient([
      op({ plugin_id: "search_plugin", operation_id: "web_search" }),
    ])
    const hit = resolveCatalogOp(client, { plugin: "search_plugin", name: "web_search" })
    expect(hit?.operation_id).toBe("web_search")
  })

  it("matches plugin__name alias", () => {
    const client = createCatalogClient([
      op({ plugin_id: "search_plugin", operation_id: "search_plugin__web_search" }),
    ])
    const hit = resolveCatalogOp(client, { plugin: "search_plugin", name: "web_search" })
    expect(hit?.operation_id).toBe("search_plugin__web_search")
  })

  it("matches mcp.tool_name", () => {
    const client = createCatalogClient([
      op({
        plugin_id: "search_plugin",
        operation_id: "op_web",
        mcp: { tool_name: "web_search" },
      }),
    ])
    const hit = resolveCatalogOp(client, { plugin: "search_plugin", name: "web_search" })
    expect(hit?.operation_id).toBe("op_web")
  })

  it("returns null when nothing matches", () => {
    const client = createCatalogClient([])
    expect(resolveCatalogOp(client, { plugin: "x", name: "y" })).toBeNull()
  })
})
