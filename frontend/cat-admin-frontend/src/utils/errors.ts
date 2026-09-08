/**
 * Error utilities
 *
 * Centralized helpers for safe error message extraction from unknown thrown values.
 */

/** Extract a human-readable message from an unknown thrown value. */
export function getErrorMessage(err: unknown, fallback = "An unexpected error occurred."): string {
  if (err instanceof Error) return err.message
  if (typeof err === "string") return err
  return fallback
}
