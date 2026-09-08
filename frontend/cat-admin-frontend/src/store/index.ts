/**
 * Global Store Entry Point
 *
 * Orchestrates the creation and composition of Zustand store slices.
 * Handles persistence for session-related data and provides shallow selectors
 * for optimized component re-renders.
 */

import { create } from "zustand"
import { persist, createJSONStorage } from "zustand/middleware"
import { useShallow } from "zustand/react/shallow"
import { createAuthSlice, type AuthSlice } from "./authSlice"
import { createPreferencesSlice, type PreferencesSlice } from "./preferencesSlice"
import { createUISlice, type UISlice } from "./uiSlice"
import { createSessionSlice, type SessionSlice } from "./sessionSlice"
import { createLiveLogsSlice, type LiveLogsSlice } from "./liveLogsSlice"

type SessionStore = AuthSlice & SessionSlice

type PreferencesStore = PreferencesSlice

/**
 * Device-persisted preferences store (localStorage key `whiskers-preferences`).
 * Persists `theme`/`accent`/`density`/`notifications`; other slice fields stay in-memory only.
 */
export const usePreferencesStore = create<PreferencesStore>()(
  persist(
    (...args) => ({
      ...createPreferencesSlice(...args),
    }),
    {
      name: "whiskers-preferences",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        theme: state.theme,
        accent: state.accent,
        density: state.density,
        notifications: state.notifications,
      }),
    },
  ),
)

export type {
  Theme,
  Accent,
  Density,
  NotificationPreferences,
  ThemeAttrs,
} from "./preferencesSlice"
export { selectThemeAttrs } from "./preferencesSlice"

/**
 * Store hook for session management.
 */
export const useSessionStore = create<SessionStore>()(
  persist(
    (...args) => ({
      ...createAuthSlice(...args),
      ...createSessionSlice(...args),
    }),
    {
      name: "whiskers-session",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        mcpState: state.mcpState,
      }),
      version: 2,
      // Drop pre-v2 blobs that stored `status: "connected"` — cookies, not sessionStorage, are auth.
      migrate: (persisted) => {
        const raw = persisted as { mcpState?: unknown } | undefined
        return {
          mcpState: typeof raw?.mcpState === "string" ? raw.mcpState : null,
        }
      },
    },
  ),
)

/**
 * Store hook for UI state.
 */
export const useUIStore = create<UISlice>()((...args) => ({
  ...createUISlice(...args),
}))

/**
 * Selector hook for retrieving authentication state with shallow comparison.
 */
export const useAuthState = () =>
  useSessionStore(
    useShallow((s) => ({
      status: s.status,
      subject: s.subject,
      scopes: s.scopes,
      expiresAt: s.expiresAt,
      iat: s.iat,
    })),
  )

/** Selector hook for the pending MCP auth state token. */
export const useMcpState = () => useSessionStore((s) => s.mcpState)

// ---------------------------------------------------------------------------
// Logs Store — persists `awake` to localStorage, everything else is transient
// ---------------------------------------------------------------------------
/**
 * Store hook for logs.
 */
export const useLogsStore = create<LiveLogsSlice>()(
  persist(
    (...args) => ({
      ...createLiveLogsSlice(...args),
    }),
    {
      name: "cat-logs-prefs",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ awake: state.awake }),
    },
  ),
)
