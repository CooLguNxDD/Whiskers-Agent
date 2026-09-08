/**
 * Terminal Slice
 *
 * Owns the multi-session terminal manager state: a record of open relay sessions,
 * the active tab, and lifecycle actions. Deliberately NOT persisted — terminals are
 * ephemeral; each session maps to a live host PTY that dies when the tab closes.
 * Each <TerminalView> owns its own Xterm instance + WebSocket and reports status
 * back here via `setStatus`.
 */

import { bus } from "@/events/bus"
import { getErrorMessage } from "@/utils/errors"
import { create } from "zustand"
import {
  killTerminalSession,
  openTerminalSession,
  elevateSession,
} from "@/api/terminal"

export type TerminalStatus = "opening" | "connected" | "closed" | "error" | "elevated" | "closing"

export type StepUpMethod = "totp" | "password" | "both"

export interface TerminalSession {
  id: string
  ideId: string
  title: string
  workdir: string | null
  status: TerminalStatus
  error?: string | null
  wsUrl: string
  wsTicket: string
  elevationExpiry: number | null
  elevationMethod: StepUpMethod | null
  elevationPrompt: boolean
  elevationError?: string | null
}

export interface TerminalSlice {
  sessions: Record<string, TerminalSession>
  activeSessionId: string | null
  tabCounter: number
  openSession: (ideId: string, workdir?: string) => Promise<void>
  closeSession: (id: string) => void
  setActive: (id: string) => void
  renameSession: (id: string, title: string) => void
  setStatus: (id: string, status: TerminalStatus, error?: string | null) => void
  /** Request step-up elevation for a session. */
  requestElevation: (id: string, method: StepUpMethod) => void
  /** Submit totp/password credentials to elevate the session. */
  submitElevation: (id: string, factors: { totp?: string; password?: string }) => Promise<void>
  /** Dismiss the elevation prompt overlay. */
  dismissElevation: (id: string) => void
  /** Clear active elevation when expired. */
  clearElevation: (id: string) => void
}

/**
 * Standalone transient store for the terminal manager (no persistence).
 */
export const useTerminalStore = create<TerminalSlice>((set) => ({
  sessions: {},
  activeSessionId: null,
  tabCounter: 0,

  openSession: async (ideId, workdir) => {
    const res = await openTerminalSession(ideId, workdir)
    set((s) => {
      const nextCounter = s.tabCounter + 1
      const session: TerminalSession = {
        id: res.session_id,
        ideId,
        title: workdir ? shortWorkdir(workdir) : `term ${nextCounter}`,
        workdir: workdir || null,
        status: "opening",
        wsUrl: res.ws_url,
        wsTicket: res.ws_ticket,
        elevationExpiry: null,
        elevationMethod: null,
        elevationPrompt: false,
      }
      return {
        tabCounter: nextCounter,
        sessions: { ...s.sessions, [session.id]: session },
        activeSessionId: session.id,
      }
    })
  },

  closeSession: (id) => {
    // Mark closing first; only drop the tab after the kill succeeds so orphans stay visible.
    set((s) => {
      const session = s.sessions[id]
      if (!session) return s
      return {
        sessions: {
          ...s.sessions,
          [id]: { ...session, status: "closing", error: null },
        },
      }
    })
    void killTerminalSession(id)
      .then(() => {
        set((s) => {
          if (!s.sessions[id]) return s
          const next = { ...s.sessions }
          delete next[id]
          const remaining = Object.keys(next)
          const activeSessionId =
            s.activeSessionId === id ? remaining[remaining.length - 1] ?? null : s.activeSessionId
          return { sessions: next, activeSessionId }
        })
      })
      .catch((err) => {
        const message = getErrorMessage(err)
        bus.emit("toast:error", { message: `Failed to kill terminal session ${id}: ${message}` })
        console.error(`Failed to kill terminal session ${id}:`, err)
        set((s) => {
          const session = s.sessions[id]
          if (!session) return s
          return {
            sessions: {
              ...s.sessions,
              [id]: { ...session, status: "error", error: message },
            },
          }
        })
      })
  },

  setActive: (id) => set({ activeSessionId: id }),

  renameSession: (id, title) =>
    set((s) =>
      s.sessions[id]
        ? { sessions: { ...s.sessions, [id]: { ...s.sessions[id], title } } }
        : s,
    ),

  setStatus: (id, status, error = null) =>
    set((s) =>
      s.sessions[id]
        ? { sessions: { ...s.sessions, [id]: { ...s.sessions[id], status, error } } }
        : s,
    ),

  requestElevation: (id, method) =>
    set((s) =>
      s.sessions[id]
        ? {
            sessions: {
              ...s.sessions,
              [id]: {
                ...s.sessions[id],
                elevationPrompt: true,
                elevationMethod: method,
                elevationError: null,
              },
            },
          }
        : s,
    ),

  submitElevation: async (id, factors) => {
    try {
      const res = await elevateSession(id, factors)
      set((s) => {
        if (!s.sessions[id]) return {}
        return {
          sessions: {
            ...s.sessions,
            [id]: {
              ...s.sessions[id],
              status: "elevated",
              elevationExpiry: res.expires_at,
              elevationPrompt: false,
              elevationError: null,
            },
          },
        }
      })
    } catch (err: unknown) {
      const msg = getErrorMessage(err)
      set((s) => {
        if (!s.sessions[id]) return {}
        return {
          sessions: {
            ...s.sessions,
            [id]: {
              ...s.sessions[id],
              elevationError: msg,
            },
          },
        }
      })
    }
  },

  dismissElevation: (id) =>
    set((s) =>
      s.sessions[id]
        ? {
            sessions: {
              ...s.sessions,
              [id]: {
                ...s.sessions[id],
                elevationPrompt: false,
                elevationError: null,
              },
            },
          }
        : s,
    ),

  clearElevation: (id) =>
    set((s) => {
      const session = s.sessions[id]
      if (!session) return s
      const nextStatus = session.status === "elevated" ? "connected" : session.status
      return {
        sessions: {
          ...s.sessions,
          [id]: {
            ...session,
            elevationExpiry: null,
            status: nextStatus,
          },
        },
      }
    }),
}))

/** Derive a compact tab title from a workdir path (last path segment). */
function shortWorkdir(workdir: string): string {
  const parts = workdir.replace(/[\\/]+$/, "").split(/[\\/]/)
  return parts[parts.length - 1] || workdir
}
