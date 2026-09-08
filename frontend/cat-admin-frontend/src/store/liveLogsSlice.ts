/**
 * LiveLogs Slice
 *
 * Owns the persistent Awake/Asleep "spell" state for the LiveLogs terminal,
 * plus transient UI state (paused, filter). Follows the react-app-guide pattern:
 *
 * - awake: localStorage-persisted (device preference — the "spell")
 * - paused: transient, resets on reload (non-persisted Zustand)
 * - filter: transient, resets on reload (non-persisted Zustand)
 */

import type { StateCreator } from "zustand"

export interface LiveLogsSlice {
  /** Whether the terminal is actively listening to the server log stream. */
  awake: boolean
  /** Whether new entries are buffered (paused) rather than displayed live. */
  paused: boolean
  /** Current text filter applied to log rows. */
  filter: string
  /** Active tag-class filter chip (empty = ALL). */
  tagFilter: string

  setAwake: (v: boolean) => void
  setPaused: (v: boolean) => void
  setFilter: (v: string) => void
  setTagFilter: (v: string) => void
}

/**
 * Creates the live logs slice for the store.
 */
export const createLiveLogsSlice: StateCreator<LiveLogsSlice> = (set) => ({
  awake: true,
  paused: false,
  filter: "",
  tagFilter: "",

  setAwake: (awake) => set({ awake }),
  setPaused: (paused) => set({ paused }),
  setFilter: (filter) => set({ filter }),
  setTagFilter: (tagFilter) => set({ tagFilter }),
})
