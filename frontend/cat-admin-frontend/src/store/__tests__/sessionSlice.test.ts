import { describe, it, expect } from 'vitest'
import { create } from 'zustand'
import { createSessionSlice, type SessionSlice } from '../sessionSlice'

describe('Session Slice', () => {
  it('should initialize with mcpState as null', () => {
    const useSessionStore = create<SessionSlice>()(createSessionSlice)
    expect(useSessionStore.getState().mcpState).toBe(null)
  })

  it('should set mcpState when setMcpState("abc123") is called', () => {
    const useSessionStore = create<SessionSlice>()(createSessionSlice)
    useSessionStore.getState().setMcpState('abc123')
    expect(useSessionStore.getState().mcpState).toBe('abc123')
  })

  it('should reset mcpState to null when clearMcpState is called after set', () => {
    const useSessionStore = create<SessionSlice>()(createSessionSlice)
    useSessionStore.getState().setMcpState('abc123')
    useSessionStore.getState().clearMcpState()
    expect(useSessionStore.getState().mcpState).toBe(null)
  })
})
