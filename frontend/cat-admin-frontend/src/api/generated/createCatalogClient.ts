/**
 * Lightweight catalog client factory — generateClient-style DX without orval/codegen.
 */

import type { CatalogOperation } from "../catalog"
import { createInferenceClient, invokeCatalogOperation } from "../inferenceClient"

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
      // Op missing from this snapshot. Host console ops are mirrored with
      // is_fast_path=False (core/route_registry/host_catalog.py), so a
      // blind POST /execute is guaranteed 501 for exactly the ops this
      // client serves — fail with a diagnosable error instead. Callers that
      // want retry-on-miss should go through `callCatalogOp`
      // (catalogRuntime.ts), which re-fetches the live snapshot first.
      throw new Error(
        `Unknown catalog operation ${pluginId}/${operationId} (missing from this snapshot)`,
      )
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