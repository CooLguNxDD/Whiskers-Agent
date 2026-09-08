import { describe, it, expect, vi, afterEach } from 'vitest'
import { nextReconnectDelay } from '../useAnalytics'

describe('nextReconnectDelay', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('nextReconnectDelay(0) is in [1000, 2000)', () => {
    const rand = vi.spyOn(Math, 'random')
    rand.mockReturnValue(0)
    expect(nextReconnectDelay(0)).toBe(1000)
    rand.mockReturnValue(0.999)
    expect(nextReconnectDelay(0)).toBe(1999)
    // bounds regardless of mock
    expect(nextReconnectDelay(0)).toBeGreaterThanOrEqual(1000)
    expect(nextReconnectDelay(0)).toBeLessThan(2000)
  })

  it('nextReconnectDelay(3) is in [8000, 9000)', () => {
    const rand = vi.spyOn(Math, 'random')
    rand.mockReturnValue(0)
    expect(nextReconnectDelay(3)).toBe(8000)
    rand.mockReturnValue(0.5)
    expect(nextReconnectDelay(3)).toBe(8500)
    expect(nextReconnectDelay(3)).toBeGreaterThanOrEqual(8000)
    expect(nextReconnectDelay(3)).toBeLessThan(9000)
  })

  it('monotonic, non-jitter base caps at 30_000: nextReconnectDelay(20) is in [30000, 31000)', () => {
    const rand = vi.spyOn(Math, 'random')
    rand.mockReturnValue(0)
    expect(nextReconnectDelay(20)).toBe(30000)
    rand.mockReturnValue(0.999)
    const v = nextReconnectDelay(20)
    expect(v).toBeGreaterThanOrEqual(30000)
    expect(v).toBeLessThan(31000)
    // caps for higher attempts
    rand.mockReturnValue(0)
    expect(nextReconnectDelay(30)).toBe(30000)
  })
})
