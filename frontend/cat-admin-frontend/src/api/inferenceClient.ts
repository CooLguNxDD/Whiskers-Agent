/**
 * Runtime inference client — call catalog operations without hardcoding paths.
 *
 * Prefer `createCatalogClient` / `callCatalogOp` in UI code; this module
 * remains the low-level transport used by the generated client factory.
 */

import type { CatalogOperation } from "./catalog"
import { executeOperation } from "./catalog"
import { api } from "./client"

export type InferenceClient = {
  /** Invoke by identity; prefers HttpExposure path when present, else catalog execute. */
  call: (
    pluginId: string,
    operationId: string,
    args?: Record<string, unknown>,
  ) => Promise<unknown>
  get: (pluginId: string, operationId: string) => CatalogOperation | undefined
  operations: CatalogOperation[]
}

const PATH_PARAM_RE = /\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g

/**
 * Fill `{param}` segments from args; leftover keys become query (GET) or body.
 */
export function resolveHttpCall(
  pathTemplate: string,
  method: string,
  args: Record<string, unknown> = {},
): { path: string; method: string; body?: Record<string, unknown> } {
  const remaining: Record<string, unknown> = { ...args }
  const path = pathTemplate.replace(PATH_PARAM_RE, (_m, key: string) => {
    if (!(key in remaining) || remaining[key] === undefined || remaining[key] === null) {
      throw new Error(`Missing path parameter "${key}" for ${pathTemplate}`)
    }
    const value = remaining[key]
    delete remaining[key]
    return encodeURIComponent(String(value))
  })

  const methodU = method.toUpperCase()
  if (methodU === "GET" || methodU === "HEAD") {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(remaining)) {
      if (v === undefined || v === null) continue
      qs.set(k, typeof v === "object" ? JSON.stringify(v) : String(v))
    }
    const q = qs.toString()
    return { path: q ? `${path}?${q}` : path, method: methodU }
  }

  return {
    path,
    method: methodU,
    body: remaining,
  }
}

/**
 * Invoke a single catalog operation via HTTP exposure or execute POST.
 */
export async function invokeCatalogOperation(
  op: CatalogOperation,
  args: Record<string, unknown> = {},
): Promise<unknown> {
  if (op.http?.method && op.http.path_template) {
    const { path, method, body } = resolveHttpCall(
      op.http.path_template,
      op.http.method,
      args,
    )
    if (method === "GET" || method === "HEAD") return api.get(path)
    if (method === "POST") return api.post(path, body ?? {})
    if (method === "PUT") return api.put(path, body ?? {})
    if (method === "PATCH") return api.patch(path, body ?? {})
    if (method === "DELETE") {
      return body && Object.keys(body).length > 0
        ? api.delete(path, body)
        : api.delete(path)
    }
  }

  const res = await executeOperation(op.plugin_id, op.operation_id, args)
  if (res && typeof res === "object" && "result" in res) {
    return (res as { result: unknown }).result
  }
  return res
}

/**
 * Build a client bound to a catalog snapshot.
 */
export function createInferenceClient(operations: CatalogOperation[]): InferenceClient {
  const byKey = new Map(
    operations.map((op) => [`${op.plugin_id}::${op.operation_id}`, op] as const),
  )

  return {
    operations,
    get(pluginId, operationId) {
      return byKey.get(`${pluginId}::${operationId}`)
    },
    async call(pluginId, operationId, args = {}) {
      const op = byKey.get(`${pluginId}::${operationId}`)
      if (!op) {
        // Fallback: server-side execute (validates + resolves without FE path knowledge)
        const res = await executeOperation(pluginId, operationId, args)
        if (res && typeof res === "object" && "result" in res) {
          return (res as { result: unknown }).result
        }
        return res
      }
      return invokeCatalogOperation(op, args)
    },
  }
}
