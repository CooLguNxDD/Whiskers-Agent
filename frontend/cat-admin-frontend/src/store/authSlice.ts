/**
 * Auth Slice
 *
 * Manages the authentication state machine (FSM) for the application.
 * Handles transitions between disconnected, authorizing, and connected states,
 * and stores session-specific metadata like subject and token expiry.
 * Server-side /admin/me is authoritative — no sessionStorage persistence.
 */

import { bus } from "@/events/bus"
import type { StateCreator } from "zustand"

/**
 * Authentication state interface.
 */
export const AuthState = {
  DISCONNECTED: "disconnected",
  AUTHORIZING: "authorizing",
  CONNECTED: "connected",
} as const

export type AuthState = (typeof AuthState)[keyof typeof AuthState]

export type AuthAction =
  | { type: "authorize" }
  | {
      type: "success"
      subject: string | null
      scopes: string[]
      expiresAt: number | null
      /** JWT issued-at (unix seconds); used for session-age display. */
      iat?: number | null
    }
  | { type: "fail" }
  | { type: "disconnect" }
  | { type: "auth_expired" }

/**
 * Reducer function for the authentication finite state machine (FSM).
 * Ensures valid state transitions based on dispatched actions.
 */
export function authFsmReducer(state: AuthState, action: AuthAction): AuthState {
  switch (state) {
    case AuthState.DISCONNECTED:
      if (action.type === "authorize") return AuthState.AUTHORIZING
      if (action.type === "success") return AuthState.CONNECTED
      break
    case AuthState.AUTHORIZING:
      if (action.type === "success") return AuthState.CONNECTED
      if (action.type === "fail") return AuthState.DISCONNECTED
      if (action.type === "disconnect") return AuthState.DISCONNECTED
      if (action.type === "auth_expired") return AuthState.DISCONNECTED
      break
    case AuthState.CONNECTED:
      if (action.type === "auth_expired") return AuthState.DISCONNECTED
      if (action.type === "disconnect") return AuthState.DISCONNECTED
      if (action.type === "fail") return AuthState.DISCONNECTED
      if (action.type === "authorize") return state // already connected
      break
  }
  return state
}

export interface AuthSlice {
  status: AuthState
  subject: string | null
  scopes: string[]
  expiresAt: number | null
  /** JWT issued-at (unix seconds); null when unknown. */
  iat: number | null
  dispatch: (action: AuthAction) => void
}

/**
 * Creates the authentication slice of the Zustand store.
 */
export const createAuthSlice: StateCreator<AuthSlice> = (set) => ({
  status: AuthState.DISCONNECTED,
  subject: null,
  scopes: [],
  expiresAt: null,
  iat: null,
  dispatch: (action) => {
    let shouldRefreshSidebar = false
    set((state) => {
      const nextStatus = authFsmReducer(state.status, action)
      if (nextStatus === state.status && action.type !== "success") {
        return state
      }

      shouldRefreshSidebar = true

      if (action.type === "success") {
        return {
          status: nextStatus,
          subject: action.subject,
          scopes: action.scopes,
          expiresAt: action.expiresAt,
          iat: action.iat ?? null,
        }
      }

      if (nextStatus === AuthState.DISCONNECTED) {
        return {
          status: nextStatus,
          subject: null,
          scopes: [],
          expiresAt: null,
          iat: null,
        }
      }

      return { status: nextStatus }
    })
    // Side effects stay outside the pure Zustand updater.
    if (shouldRefreshSidebar) {
      bus.emit("sidebar:refresh", undefined)
    }
  },
})
