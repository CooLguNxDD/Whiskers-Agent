/**
 * Live tool registry hook — fetches every exposed MCP tool (with full JSON
 * schemas) from the backend and maps it to the playground's PGTool shape so
 * SchemaTree, dummyFromSchema, and the tool rails keep working unchanged.
 */

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { fetchPlaygroundTools } from "@/api/playground"
import type { JSONSchema, PGTool } from "./types"
import { useAuthedQueryEnabled } from "@/hooks/useAuthedQuery"

const EMPTY_SCHEMA: JSONSchema = { type: "object", properties: {} }

function asSchema(raw: Record<string, unknown> | undefined): JSONSchema {
  if (!raw || typeof raw !== "object" || !("properties" in raw)) return EMPTY_SCHEMA
  return raw as unknown as JSONSchema
}

/**
 * Hook for managing the tool registry.
 */
export function useToolRegistry(): { tools: PGTool[]; loading: boolean; error: string | null } {
  const enabled = useAuthedQueryEnabled()
  const { data, isLoading, error } = useQuery({
    queryKey: ["playground-tools"],
    queryFn: fetchPlaygroundTools,
    staleTime: 60_000,
    enabled,
  })

  const tools: PGTool[] = useMemo(
    () =>
      (data?.tools ?? []).map((t) => ({
        name: t.name,
        plugin: t.plugin,
        desc: t.description,
        input: asSchema(t.input_schema),
        output: asSchema(t.output_schema),
      })),
    [data],
  )

  return { tools, loading: isLoading, error: error ? String(error) : null }
}
