/**
 * Map a playground MCP tool to a live catalog (plugin_id, operation_id).
 */

import type { CatalogClient } from "@/api/generated/createCatalogClient"
import type { CatalogOperation } from "@/api/catalog"

/**
 * Reference to a playground tool by plugin namespace and operation name.
 */
export type PlaygroundToolRef = {
  plugin: string
  name: string
}

/**
 * Resolve the catalog op for a playground rail tool. Exact (plugin, name) first,
 * then `plugin__name`, then MCP tool_name. Null when the catalog has no match —
 * callers may fall back to the playground invoke route.
 */
export function resolveCatalogOp(
  client: CatalogClient,
  tool: PlaygroundToolRef,
): CatalogOperation | null {
  const direct = client.get(tool.plugin, tool.name)
  if (direct) return direct
  const aliased = client.get(tool.plugin, `${tool.plugin}__${tool.name}`)
  if (aliased) return aliased
  return (
    client.operations.find((op) => {
      if (op.plugin_id !== tool.plugin) return false
      return (
        op.operation_id === tool.name ||
        op.mcp?.tool_name === tool.name ||
        op.mcp?.qualified_name === tool.name
      )
    }) ?? null
  )
}
