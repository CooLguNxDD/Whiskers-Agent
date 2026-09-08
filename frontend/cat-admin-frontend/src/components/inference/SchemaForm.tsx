/**
 * Baseline JSON Schema form host for catalog-driven operations.
 *
 * Supports property order, titles, defaults, enums, async options, secret
 * fields, and x-whiskers-ui extensions. Unknown widgets degrade to a JSON textarea.
 */

import { getErrorMessage } from "@/utils/errors"
import { useEffect, useMemo, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export type AsyncOptionsSpec = {
  plugin_id: string
  operation_id: string
  valueKey: string
  labelKey: string
  args?: Record<string, unknown>
}

export type AsyncOption = {
  value: string
  label: string
}

export type JsonSchema = {
  type?: string
  title?: string
  description?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  enum?: unknown[]
  default?: unknown
  format?: string
  "x-whiskers-ui"?: {
    order?: string[]
    secret?: boolean
    label?: string
    asyncOptions?: AsyncOptionsSpec
  }
}

export type SchemaFormProps = {
  schema: JsonSchema | Record<string, unknown>
  initialValues?: Record<string, unknown>
  submitLabel?: string
  onSubmit: (values: Record<string, unknown>) => void | Promise<void>
  disabled?: boolean
  loadAsyncOptions?: (spec: AsyncOptionsSpec) => Promise<AsyncOption[]>
}

function asSchema(raw: Record<string, unknown>): JsonSchema {
  return raw as JsonSchema
}

/** Stable empty object so `?? {}` does not bust useMemo/useEffect deps each render. */
const EMPTY_PROPS: Record<string, JsonSchema> = {}

/**
 * Render a simple object-schema form with x-whiskers-ui hints.
 */
export function SchemaForm({
  schema: rawSchema,
  initialValues,
  submitLabel = "Run",
  onSubmit,
  disabled,
  loadAsyncOptions,
}: SchemaFormProps) {
  const schema = asSchema(rawSchema as Record<string, unknown>)
  const properties = schema.properties ?? EMPTY_PROPS
  const required = new Set(schema.required ?? [])
  const ui = schema["x-whiskers-ui"]
  const order =
    ui?.order ??
    Object.keys(properties)

  const keys = useMemo(() => {
    const ordered = order.filter((k) => k in properties)
    for (const k of Object.keys(properties)) {
      if (!ordered.includes(k)) ordered.push(k)
    }
    return ordered
  }, [order, properties])

  const defaults = useMemo(() => {
    const d: Record<string, unknown> = { ...(initialValues ?? {}) }
    for (const [k, prop] of Object.entries(properties)) {
      if (d[k] === undefined && prop.default !== undefined) {
        d[k] = prop.default
      }
    }
    return d
  }, [properties, initialValues])

  const [values, setValues] = useState<Record<string, unknown>>(defaults)
  const [rawJson, setRawJson] = useState(() => JSON.stringify(defaults, null, 2))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [asyncOptions, setAsyncOptions] = useState<Record<string, AsyncOption[]>>({})
  const [asyncLoading, setAsyncLoading] = useState<Record<string, boolean>>({})
  const asyncLoadedRef = useRef<Set<string>>(new Set())

  // Reset form state when schema defaults change (e.g. switching catalog actions).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setValues(defaults)
    setRawJson(JSON.stringify(defaults, null, 2))
    setError(null)
    asyncLoadedRef.current.clear()
    setAsyncOptions({})
    setAsyncLoading({})
  }, [defaults])

  useEffect(() => {
    if (!loadAsyncOptions) return

    for (const key of keys) {
      const spec = properties[key]?.["x-whiskers-ui"]?.asyncOptions
      if (!spec || asyncLoadedRef.current.has(key)) continue
      asyncLoadedRef.current.add(key)

      setAsyncLoading((prev) => ({ ...prev, [key]: true }))
      void loadAsyncOptions(spec)
        .then((opts) => {
          setAsyncOptions((prev) => ({ ...prev, [key]: opts }))
        })
        .catch((err) => {
          setError(getErrorMessage(err))
        })
        .finally(() => {
          setAsyncLoading((prev) => ({ ...prev, [key]: false }))
        })
    }
  }, [keys, properties, loadAsyncOptions])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await onSubmit(values)
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
      {keys.map((key) => {
        const prop = properties[key] ?? {}
        const propUi = prop["x-whiskers-ui"]
        const label = propUi?.label ?? prop.title ?? key
        const isSecret =
          propUi?.secret === true || prop.format === "password"
        const isRequired = required.has(key)
        const enums = prop.enum
        const asyncSpec = propUi?.asyncOptions
        const fieldAsyncOptions = asyncOptions[key] ?? []
        const isAsyncLoading = asyncLoading[key] === true

        return (
          <div key={key} className="flex flex-col gap-1">
            <Label htmlFor={`sf-${key}`}>
              {label}
              {isRequired ? " *" : ""}
            </Label>
            {asyncSpec ? (
              <>
                <select
                  id={`sf-${key}`}
                  className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
                  value={String(values[key] ?? "")}
                  disabled={disabled || busy || isAsyncLoading}
                  onChange={(ev) =>
                    setValues((v) => ({ ...v, [key]: ev.target.value }))
                  }
                >
                  <option value="">
                    {isAsyncLoading ? "Loading…" : "—"}
                  </option>
                  {fieldAsyncOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                {!loadAsyncOptions ? (
                  <p className="text-xs text-muted-foreground">
                    Async options unavailable — provide loadAsyncOptions.
                  </p>
                ) : null}
              </>
            ) : enums && enums.length > 0 ? (
              <select
                id={`sf-${key}`}
                className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
                value={String(values[key] ?? "")}
                disabled={disabled || busy}
                onChange={(ev) =>
                  setValues((v) => ({ ...v, [key]: ev.target.value }))
                }
              >
                <option value="">—</option>
                {enums.map((opt) => (
                  <option key={String(opt)} value={String(opt)}>
                    {String(opt)}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                id={`sf-${key}`}
                type={isSecret ? "password" : "text"}
                value={values[key] == null ? "" : String(values[key])}
                disabled={disabled || busy}
                onChange={(ev) =>
                  setValues((v) => ({ ...v, [key]: ev.target.value }))
                }
              />
            )}
            {prop.description ? (
              <p className="text-xs text-muted-foreground">{prop.description}</p>
            ) : null}
          </div>
        )
      })}

      {keys.length === 0 ? (
        <textarea
          className="min-h-24 rounded-lg border border-input bg-transparent p-2 font-mono text-xs"
          value={rawJson}
          disabled={disabled || busy}
          onChange={(ev) => {
            const val = ev.target.value
            setRawJson(val)
            try {
              setValues(JSON.parse(val) as Record<string, unknown>)
              setError(null)
            } catch {
              setError("Invalid JSON")
            }
          }}
        />
      ) : null}

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Button type="submit" disabled={disabled || busy} size="sm">
        {busy ? "Running…" : submitLabel}
      </Button>
    </form>
  )
}
