/** Shared guards for NodeRegistry derive — avoid initial-state false positives. */

/** Type guard: value is an array with at least one element (an empty/absent array reads as "no data yet", not "empty result"). */
export function hasItems(v: unknown): v is unknown[] {
  return Array.isArray(v) && v.length > 0
}

/** Type guard: plain non-array object with at least one own key. */
export function isNonEmptyObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length > 0
}

/** True once the planner (or builder) has produced a runnable workflow. */
export function planStarted(s: Record<string, unknown>): boolean {
  return hasItems(s.instruction_set) || hasItems(s.plan) || !!s.yaml_workflow
}