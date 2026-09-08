/**
 * UI Slice
 *
 * Manages transient UI state that doesn't need to be persisted across sessions.
 * Currently handles tracking which plugin card is expanded in the console view.
 */

import type { StateCreator } from "zustand"

export interface UISlice {
  expandedPluginId: string | null
  setExpandedPlugin: (id: string | null) => void

  // Sidebar
  sidebarCollapsed: boolean
  setSidebarCollapsed: (collapsed: boolean) => void
  toggleSidebarCollapsed: () => void

  // Playground panels
  playgroundChatHistoryCollapsed: boolean
  setPlaygroundChatHistoryCollapsed: (collapsed: boolean) => void
  togglePlaygroundChatHistoryCollapsed: () => void

  playgroundRegistryCollapsed: boolean
  setPlaygroundRegistryCollapsed: (collapsed: boolean) => void
  togglePlaygroundRegistryCollapsed: () => void

  playgroundToolRegistryCollapsed: boolean
  setPlaygroundToolRegistryCollapsed: (collapsed: boolean) => void
  togglePlaygroundToolRegistryCollapsed: () => void

  playgroundResponseCollapsed: boolean
  setPlaygroundResponseCollapsed: (collapsed: boolean) => void
  togglePlaygroundResponseCollapsed: () => void
}

/**
 * Creates the UI slice of the Zustand store.
 */
export const createUISlice: StateCreator<UISlice> = (set) => ({
  expandedPluginId: null,
  setExpandedPlugin: (id) => set({ expandedPluginId: id }),

  sidebarCollapsed: false,
  setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
  toggleSidebarCollapsed: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),

  playgroundChatHistoryCollapsed: false,
  setPlaygroundChatHistoryCollapsed: (playgroundChatHistoryCollapsed) => set({ playgroundChatHistoryCollapsed }),
  togglePlaygroundChatHistoryCollapsed: () => set((state) => ({ playgroundChatHistoryCollapsed: !state.playgroundChatHistoryCollapsed })),

  playgroundRegistryCollapsed: false,
  setPlaygroundRegistryCollapsed: (playgroundRegistryCollapsed) => set({ playgroundRegistryCollapsed }),
  togglePlaygroundRegistryCollapsed: () => set((state) => ({ playgroundRegistryCollapsed: !state.playgroundRegistryCollapsed })),

  playgroundToolRegistryCollapsed: false,
  setPlaygroundToolRegistryCollapsed: (playgroundToolRegistryCollapsed) => set({ playgroundToolRegistryCollapsed }),
  togglePlaygroundToolRegistryCollapsed: () => set((state) => ({ playgroundToolRegistryCollapsed: !state.playgroundToolRegistryCollapsed })),

  playgroundResponseCollapsed: false,
  setPlaygroundResponseCollapsed: (playgroundResponseCollapsed) => set({ playgroundResponseCollapsed }),
  togglePlaygroundResponseCollapsed: () => set((state) => ({ playgroundResponseCollapsed: !state.playgroundResponseCollapsed })),
})
