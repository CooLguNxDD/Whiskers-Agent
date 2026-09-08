/**
 * Schema-driven actions panel for a plugin — lists catalog ops with UI slot
 * plugin.detail.actions (or any fast-path form ops when none declare UI).
 *
 * Uses createCatalogClient (generateClient-style) for all invoke paths.
 */

import { getErrorMessage } from "@/utils/errors"
import { useCallback, useMemo, useState } from "react"
import { useCatalogClient } from "@/hooks/useCatalog"
import { SchemaForm } from "./SchemaForm"
import type { AsyncOption, AsyncOptionsSpec } from "./SchemaForm"
import type { CatalogOperation } from "@/api/catalog"

const DEFAULT_SLOT = "plugin.detail.actions"

function mapAsyncOptionsResult(
  result: unknown,
  spec: AsyncOptionsSpec,
): AsyncOption[] {
  const items = Array.isArray(result)
    ? result
    : result &&
        typeof result === "object" &&
        Array.isArray((result as { results?: unknown }).results)
      ? (result as { results: unknown[] }).results
      : []

  return items.map((item) => {
    if (item && typeof item === "object") {
      const rec = item as Record<string, unknown>
      const value = rec[spec.valueKey]
      const label = rec[spec.labelKey] ?? value
      return { value: String(value ?? ""), label: String(label ?? "") }
    }
    return { value: String(item), label: String(item) }
  })
}

export type CatalogActionsPanelProps = {
  pluginId: string
  slot?: string
}

/**
 * Render catalog-backed action forms for one plugin via the generated catalog client.
 */
export function CatalogActionsPanel({
  pluginId,
  slot = DEFAULT_SLOT,
}: CatalogActionsPanelProps) {
  const { client, revision, isLoading, error, operations } = useCatalogClient({
    pluginId,
  })
  const [selected, setSelected] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<string | null>(null)

  const ops = useMemo(() => {
    const slotted = operations.filter((o) => o.ui?.slot === slot)
    if (slotted.length > 0) return slotted
    // Baseline: show form-capable fast-path ops for this plugin
    return operations.filter((o) => o.is_fast_path)
  }, [operations, slot])

  const loadAsyncOptions = useCallback(
    async (spec: AsyncOptionsSpec) => {
      // Bound caller from generateClient: client.op(plugin, op)(args)
      const result = await client.op(spec.plugin_id, spec.operation_id)(
        spec.args ?? {},
      )
      return mapAsyncOptionsResult(result, spec)
    },
    [client],
  )

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading catalog…</p>
  }
  if (error) {
    return (
      <p className="text-sm text-destructive">
        Failed to load catalog:{" "}
        {getErrorMessage(error)}
      </p>
    )
  }
  if (ops.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No catalog actions for this plugin yet.
      </p>
    )
  }

  const active: CatalogOperation | undefined =
    ops.find((o) => o.operation_id === selected) ?? ops[0]

  const runActive = active
    ? client.op(active.plugin_id, active.operation_id)
    : null

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {ops.map((op) => (
          <button
            key={op.operation_id}
            type="button"
            className={`rounded-md border px-2 py-1 text-xs ${
              active?.operation_id === op.operation_id
                ? "border-ring bg-muted"
                : "border-input"
            }`}
            onClick={() => {
              setSelected(op.operation_id)
              setLastResult(null)
            }}
          >
            {op.mcp?.tool_name ?? op.operation_id}
          </button>
        ))}
      </div>

      {active && runActive ? (
        <>
          <p className="text-sm text-muted-foreground">{active.description}</p>
          <SchemaForm
            key={active.operation_id}
            schema={active.input_schema as Record<string, unknown>}
            submitLabel={`Run ${active.mcp?.tool_name ?? active.operation_id}`}
            loadAsyncOptions={loadAsyncOptions}
            onSubmit={async (values) => {
              const result = await runActive(values)
              setLastResult(JSON.stringify(result, null, 2))
            }}
          />
          {lastResult ? (
            <pre className="max-h-48 overflow-auto rounded-lg border border-input p-2 text-xs">
              {lastResult}
            </pre>
          ) : null}
        </>
      ) : null}

      {revision > 0 ? (
        <p className="text-[10px] text-muted-foreground">
          catalog revision {revision}
        </p>
      ) : null}
    </div>
  )
}
