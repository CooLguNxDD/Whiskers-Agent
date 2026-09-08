import { describe, it, expect } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useSessionStore } from "@/store"
import { useAuthedQueryEnabled } from "../useAuthedQuery"

describe("useAuthedQueryEnabled", () => {
  it("stays false until the /me probe finishes even if status is connected", () => {
    useSessionStore.setState({
      status: "connected",
      authProbeDone: false,
    })
    const { result, rerender } = renderHook(() => useAuthedQueryEnabled())
    expect(result.current).toBe(false)

    act(() => {
      useSessionStore.getState().markAuthProbeDone()
    })
    rerender()
    expect(result.current).toBe(true)
  })
})
