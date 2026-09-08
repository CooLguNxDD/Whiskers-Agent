/**
 * Utility helpers for the Playground feature.
 */

import type { JSONSchemaProperty } from "./types"

/**
 * Generates a plausible dummy value for a given JSON Schema property.
 * Used by ToolTestMode to auto-populate the editable payload editor.
 */
export function dummyFromSchema(
  schema: JSONSchemaProperty | undefined,
  key = ""
): unknown {
  if (!schema) return null
  if (schema.enum) return schema.enum[0]

  switch (schema.type) {
    case "string":
      if (schema.format === "uuid") return "c4f7a0d2-9e15-4b8e-a103-1f3a7b22d019"
      if (schema.format === "email") return "ops@whiskers.local"
      if (schema.format === "date") return new Date().toISOString().slice(0, 10)
      if (schema.format === "date-time") return new Date().toISOString()
      if (schema.format === "uri") return "https://whiskers.agent/r/abc123"
      if (schema.pattern) return "J45"
      return key === "cohort_id" ? "cohort_high_risk_q3" : "lorem ipsum"
    case "integer":
      return Math.max(schema.minimum ?? 1, Math.min(schema.maximum ?? 10, 7))
    case "number":
      return 3.14
    case "boolean":
      return true
    case "array":
      return [dummyFromSchema(schema.items ?? { type: "string" }, key)]
    case "object": {
      const out: Record<string, unknown> = {}
      const props = schema.properties ?? {}
      const required = new Set(schema.required ?? [])
      for (const k of Object.keys(props)) {
        // Only include required fields in dummy (or all if none required)
        if (required.size === 0 || required.has(k)) {
          out[k] = dummyFromSchema(props[k], k)
        }
      }
      return out
    }
    default:
      return null
  }
}

/** Returns the display string for a JSON Schema property type. */
export function schemaTypeLabel(prop: JSONSchemaProperty): string {
  if (prop.enum) return `enum(${prop.enum.length})`
  const base = prop.type ?? "any"
  return prop.format ? `${base} · ${prop.format}` : base
}
