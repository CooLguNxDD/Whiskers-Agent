/**
 * scopeLabels — display-label formatting for global scope ids.
 */

/** Hand-authored display names for scope ids that don't title-case cleanly from their raw `domain:access` form. Anything not listed here falls back to `formatScopeLabel`'s split-and-title-case default. */
export const GLOBAL_SCOPE_LABELS: Record<string, string> = {
  "whiskers": "Whiskers Agent",
  "terminal:use": "Terminal: Use",
  "terminal:host": "Terminal: Host",
  "agy": "Agy",
  "antigravity": "Antigravity",
}

/** Formats a scope id into a display label, falling back to a title-cased colon split. */
export function formatScopeLabel(id: string): string {
  return GLOBAL_SCOPE_LABELS[id] || id.split(":").map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(": ")
}
