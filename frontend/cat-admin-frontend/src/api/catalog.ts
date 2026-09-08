/**
 * Live operation catalog API — entitlement-filtered contract layer for inference UI.
 */

import { request } from "./client"
import type { OpenAPIObject } from "./generated/openapiTypes"

export type AccessClass = "read" | "write" | "admin"
export type Visibility = "public_catalog" | "authenticated" | "hidden"
export type RendererKind = "form" | "action" | "table" | "custom"

export interface HttpExposureDto {
  method: string
  path_template: string
  auth_policy: string
  request_map?: Record<string, unknown>
  response_map?: Record<string, unknown>
}

export interface McpExposureDto {
  tool_name: string
  qualified_name?: string
}

export interface UiContributionDto {
  slot: string
  renderer_kind: RendererKind | string
  ui_schema?: Record<string, unknown>
}

export interface CatalogOperation {
  plugin_id: string
  operation_id: string
  description: string
  input_schema: Record<string, unknown>
  output_schema?: Record<string, unknown> | null
  access: AccessClass | string
  required_scopes: string[]
  visibility: Visibility | string
  version: string
  tags: string[]
  http: HttpExposureDto | null
  mcp: McpExposureDto | null
  ui: UiContributionDto | null
  is_fast_path: boolean
  descriptor_hash: string
}

export interface CatalogResponse {
  revision: number
  etag: string
  operations: CatalogOperation[]
}

export interface ExecuteResult {
  status: string
  result?: unknown
  error?: string
  message?: string
  details?: unknown
}

/**
 * Fetch the live catalog (optional plugin_id / UI slot filters).
 *
 * Returns `null` only for a real 304 (an explicit `opts.etag` matched the
 * server's current ETag) — meaning "not modified, reuse your prior
 * snapshot", never "empty". `cache: "no-store"` keeps the browser's own
 * HTTP cache from injecting a silent revalidation (and thus a surprise
 * 304) on a call that never asked for one.
 */
export async function getCatalog(opts?: {
  pluginId?: string
  slot?: string
  etag?: string
}): Promise<CatalogResponse | null> {
  const params = new URLSearchParams()
  if (opts?.pluginId) params.set("plugin_id", opts.pluginId)
  if (opts?.slot) params.set("slot", opts.slot)
  const qs = params.toString()
  const path = `/api/catalog/session_gated${qs ? `?${qs}` : ""}`

  const headers: Record<string, string> = {}
  if (opts?.etag) {
    headers["If-None-Match"] = opts.etag
  }

  try {
    return await request<CatalogResponse>(path, { headers, cache: "no-store" })
  } catch (err) {
    if (err instanceof Error && err.message === "Request failed: 304") {
      return null
    }
    throw err
  }
}

/**
 * Execute an operation by (plugin_id, operation_id) via the catalog execute plane.
 */
export function executeOperation(
  pluginId: string,
  operationId: string,
  args: Record<string, unknown> = {},
): Promise<ExecuteResult> {
  return request<ExecuteResult>("/api/catalog/session_gated/execute", {
    method: "POST",
    body: JSON.stringify({
      plugin_id: pluginId,
      operation_id: operationId,
      args,
    }),
  })
}

/**
 * Fetch entitlement-filtered OpenAPI 3.0 document for HTTP-exposed catalog operations.
 */
export function getCatalogOpenApi(pluginId?: string): Promise<OpenAPIObject> {
  const params = new URLSearchParams()
  if (pluginId) params.set("plugin_id", pluginId)
  const qs = params.toString()
  const path = `/api/catalog/session_gated/openapi${qs ? `?${qs}` : ""}`
  return request<OpenAPIObject>(path)
}
