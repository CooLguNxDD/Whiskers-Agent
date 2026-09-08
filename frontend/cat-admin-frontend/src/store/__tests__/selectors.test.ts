import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useAuthState, useMcpState, useSessionStore } from '../index'

describe('store selectors', () => {
  beforeEach(() => {
    // Reset store state between tests. The live useSessionStore is a singleton
    // persisted to sessionStorage (jsdom), so tests can leak across without this.
    if (typeof useSessionStore.persist?.clearStorage === 'function') {
      useSessionStore.persist.clearStorage()
    }
    useSessionStore.setState({
      mcpState: null,
      status: 'disconnected',
      subject: null,
      scopes: [],
      expiresAt: null,
      iat: null,
      authProbeDone: false,
    })
  })

  it('useAuthState() initially returns disconnected auth fields including iat', () => {
    const { result } = renderHook(() => useAuthState())
    expect(result.current).toEqual({
      status: 'disconnected',
      subject: null,
      scopes: [],
      expiresAt: null,
      iat: null,
    })
  })

  it('useMcpState() returns "tok" after setMcpState + rerender', () => {
    const { result, rerender } = renderHook(() => useMcpState())
    expect(result.current).toBe(null)

    act(() => {
      useSessionStore.getState().setMcpState('tok')
    })
    rerender()

    expect(result.current).toBe('tok')
  })

  it('useMcpState() returns null after clearMcpState', () => {
    const { result, rerender } = renderHook(() => useMcpState())

    act(() => {
      useSessionStore.getState().setMcpState('tok')
    })
    rerender()
    expect(result.current).toBe('tok')

    act(() => {
      useSessionStore.getState().clearMcpState()
    })
    rerender()

    expect(result.current).toBe(null)
  })
})
