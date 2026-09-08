import { describe, it, expect } from 'vitest'
import { authFsmReducer, AuthState, type AuthAction } from '../authSlice'

describe('Auth FSM Reducer', () => {
  it('should initialize in DISCONNECTED state', () => {
    expect(AuthState.DISCONNECTED).toBe('disconnected')
  })

  it('should transition from DISCONNECTED to AUTHORIZING on authorize action', () => {
    const nextState = authFsmReducer(AuthState.DISCONNECTED, { type: 'authorize' })
    expect(nextState).toBe(AuthState.AUTHORIZING)
  })

  it('should transition from AUTHORIZING to CONNECTED on success action', () => {
    const successAction: AuthAction = {
      type: 'success',
      subject: 'admin',
      scopes: ['whiskers'],
      expiresAt: Date.now() + 3600000
    }
    const nextState = authFsmReducer(AuthState.AUTHORIZING, successAction)
    expect(nextState).toBe(AuthState.CONNECTED)
  })

  it('should reset to DISCONNECTED on fail while connected', () => {
    const nextState = authFsmReducer(AuthState.CONNECTED, { type: 'fail' })
    expect(nextState).toBe(AuthState.DISCONNECTED)
  })

  it('should reset to DISCONNECTED on disconnect action', () => {
    const nextState = authFsmReducer(AuthState.CONNECTED, { type: 'disconnect' })
    expect(nextState).toBe(AuthState.DISCONNECTED)
  })

  it('should reject invalid transitions (e.g. authorize while connected)', () => {
    const nextState = authFsmReducer(AuthState.CONNECTED, { type: 'authorize' })
    expect(nextState).toBe(AuthState.CONNECTED) // remains connected
  })
})
