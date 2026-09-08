/**
 * Session Slice
 *
 * Stores the pending MCP auth state token across navigation.
 * Captured from the `?state=` URL param on arrival and cleared after Layer 1 completes.
 */

import type { StateCreator } from "zustand"

export interface SessionSlice {
  mcpState: string | null
  setMcpState: (state: string | null) => void
  clearMcpState: () => void
  /** False until RootLayout's `/me` probe settles. Never persisted. */
  authProbeDone: boolean
  /** Marks the initial auth verification probe as resolved. */
  markAuthProbeDone: () => void
}

/**
 * Creates the session slice of the Zustand store.
 */
export const createSessionSlice: StateCreator<SessionSlice> = (set) => ({
  mcpState: null,
  setMcpState: (mcpState) => set({ mcpState }),
  clearMcpState: () => set({ mcpState: null }),
  authProbeDone: false,
  markAuthProbeDone: () => set({ authProbeDone: true }),
})
