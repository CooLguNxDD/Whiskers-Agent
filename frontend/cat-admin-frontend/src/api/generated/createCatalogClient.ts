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

function unknownOpError(pluginId: string, operationId: string): Error {
  return new Error(
    `Unknown catalog operation ${pluginId}/${operationId} (missing from this snapshot)`,
  )
}

/**
 * Resolve an op missing from this client's snapshot via the live module-level
 * catalog (peek, then refresh-once). Never falls back to a blind `/execute`
 * POST — host console ops are 501 there.
 */
async function resolveLiveOp(
  pluginId: string,
  operationId: string,
): Promise<CatalogOperation> {
  const runtime = await import("../catalogRuntime")
  const peeked = runtime.peekCatalogClient()?.get(pluginId, operationId)
  if (peeked) return peeked
  runtime.invalidateCatalogClient()
  const fresh = await runtime.ensureCatalogClient()
  const op = fresh.get(pluginId, operationId)
  if (!op) throw unknownOpError(pluginId, operationId)
  return op
}

/**
 * Build a catalog client from a snapshot of operations (catalog or OpenAPI-derived).
 */
export function createCatalogClient(operations: CatalogOperation[]): CatalogClient {
  const inner = createInferenceClient(operations)
  const ops = new Map(
    operations.map((op) => [opKey(op.plugin_id, op.operation_id), op] as const),
  )

  const call = async (
    pluginId: string,
    operationId: string,
    args: Record<string, unknown> = {},
  ): Promise<unknown> => {
    const local = ops.get(opKey(pluginId, operationId))
    const op = local ?? (await resolveLiveOp(pluginId, operationId))
    return invokeCatalogOperation(op, args)
  }

  return {
    operations: inner.operations,
    ops,
    get: inner.get,
    call,
    op(pluginId, operationId) {
      return (args = {}) => call(pluginId, operationId, args)
    },
  }
}