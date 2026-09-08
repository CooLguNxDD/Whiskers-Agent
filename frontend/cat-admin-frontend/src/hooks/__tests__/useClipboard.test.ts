import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useClipboard, useKeyedClipboard } from "../useClipboard"

describe("useClipboard", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
    window.isSecureContext = true
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("copies text and resets after timeout", async () => {
    const { result } = renderHook(() => useClipboard())
    expect(result.current.copied).toBe(false)

    await act(async () => {
      await result.current.copy("hello world")
    })

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("hello world")
    expect(result.current.copied).toBe(true)

    act(() => {
      vi.advanceTimersByTime(2000)
    })

    expect(result.current.copied).toBe(false)
  })
})

describe("useKeyedClipboard", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
    window.isSecureContext = true
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("copies text with keyed id and resets after timeout", async () => {
    const { result } = renderHook(() => useKeyedClipboard())
    expect(result.current.copiedId).toBe(null)

    await act(async () => {
      await result.current.copy("foo", "row-1")
    })

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("foo")
    expect(result.current.copiedId).toBe("row-1")

    // Advance timer past 1500ms
    act(() => {
      vi.advanceTimersByTime(1500)
    })

    expect(result.current.copiedId).toBe(null)
  })

  it("resets timeout if invoked repeatedly", async () => {
    const { result } = renderHook(() => useKeyedClipboard())

    await act(async () => {
      await result.current.copy("first", "row-1")
    })
    expect(result.current.copiedId).toBe("row-1")

    act(() => {
      vi.advanceTimersByTime(1000)
    })

    await act(async () => {
      await result.current.copy("second", "row-2")
    })
    expect(result.current.copiedId).toBe("row-2")

    // After 1000ms, row-2 is still active (1500ms window renewed)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.copiedId).toBe("row-2")

    // After another 500ms, it resets to null
    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(result.current.copiedId).toBe(null)
  })
})
