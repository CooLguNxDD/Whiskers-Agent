/**
 * Lightweight catalog client factory — generateClient-style DX without orval/codegen.
 */

import type { CatalogOperation } from "../catalog"
import { createInferenceClient, invokeCatalogOperation } from "../inferenceClient"
import { executeOperation } from "../catalog"

/** Bound caller returned by `CatalogClient.op`. */
export type BoundOpCaller = (args?: Record<string, unknown>) => Promise<unknown>

/** Catalog-backed client with bound operation helpers. */
export type CatalogClient = {
  call: (
    pluginId: string,
    operationId: string,
    args?: Record<string, unknown>,
  ) => Promise<unknown>
  op: (pluginId: string, operationId: string) => BoundOpCaller
  get: (pluginId: string, operationId: string) => CatalogOperation | undefined
  ops: Map<string, CatalogOperation>
  operations: CatalogOperation[]
}

function opKey(pluginId: string, operationId: string): string {
  return `${pluginId}::${operationId}`
}

/**
 * Build a catalog client from a snapshot of operations (catalog or OpenAPI-derived).
 */
export function createCatalogClient(operations: CatalogOperation[]): CatalogClient {
  const inner = createInferenceClient(operations)
  const ops = new Map(
    operations.map((op) => [opKey(op.plugin_id, op.operation_id), op] as const),
  )

  return {
    operations: inner.operations,
    ops,
    get: inner.get,
    call: async (pluginId, operationId, args = {}) => {
      const op = ops.get(opKey(pluginId, operationId))
      if (op) return invokeCatalogOperation(op, args)
      // Op missing from snapshot (stale filter) — server execute still works
      const res = await executeOperation(pluginId, operationId, args)
      if (res && typeof res === "object" && "result" in res) {
        return (res as { result: unknown }).result
      }
      return res
    },
    op(pluginId, operationId) {
      return (args = {}) => {
        const op = ops.get(opKey(pluginId, operationId))
        if (op) return invokeCatalogOperation(op, args)
        return inner.call(pluginId, operationId, args)
      }
    },
  }
}