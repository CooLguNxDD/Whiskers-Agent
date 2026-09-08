/**
 * Gate session-gated TanStack queries until `/api/admin/public/me` finishes.
 *
 * Auth is HttpOnly cookies. A persisted `connected` flag is never enough —
 * catalog/health/TOTP must wait for `authProbeDone`.
 */

import { useSessionStore } from "@/store"

/** True only after the root `/me` probe and while the live FSM is connected. */
export function useAuthedQueryEnabled(): boolean {
  return useSessionStore((s) => s.authProbeDone && s.status === "connected")
}
