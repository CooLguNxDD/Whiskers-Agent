import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { useTerminalStore } from "../terminalSlice"
import { bus } from "@/events/bus"
import { killTerminalSession } from "@/api/terminal"

vi.mock("@/api/terminal", () => ({
  killTerminalSession: vi.fn(),
  openTerminalSession: vi.fn(),
  elevateSession: vi.fn(),
}))

vi.mock("@/events/bus", () => ({
  bus: {
    emit: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
  },
}))

describe("terminalSlice closeSession", () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("calls killTerminalSession and emits a toast:error when it rejects", async () => {
    const error = new Error("Network error")
    vi.mocked(killTerminalSession).mockRejectedValue(error)

    // Setup initial store state with a session
    useTerminalStore.setState({
      sessions: {
        "session-1": {
          id: "session-1",
          ideId: "ide-1",
          title: "term 1",
          workdir: null,
          status: "connected",
          wsUrl: "ws://localhost/ws",
          wsTicket: "ticket",
          elevationExpiry: null,
          elevationMethod: null,
          elevationPrompt: false,
        },
      },
      activeSessionId: "session-1",
    })

    // Close the session
    useTerminalStore.getState().closeSession("session-1")

    expect(killTerminalSession).toHaveBeenCalledWith("session-1")

    // The store should synchronously update the session status to "closing"
    expect(useTerminalStore.getState().sessions["session-1"]).toBeDefined()
    expect(useTerminalStore.getState().sessions["session-1"].status).toBe("closing")
    expect(useTerminalStore.getState().activeSessionId).toBe("session-1")

    // Wait for the microtasks to flush so the rejected promise is handled
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(bus.emit).toHaveBeenCalledWith("toast:error", { message: `Failed to kill terminal session session-1: Network error` })
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "Failed to kill terminal session session-1:",
      error,
    )

    // After failure, the session status should update to "error"
    expect(useTerminalStore.getState().sessions["session-1"]).toBeDefined()
    expect(useTerminalStore.getState().sessions["session-1"].status).toBe("error")
    expect(useTerminalStore.getState().sessions["session-1"].error).toBe("Network error")
  })

  it("calls killTerminalSession and removes the session from state when it resolves successfully", async () => {
    vi.mocked(killTerminalSession).mockResolvedValue(undefined)

    // Setup initial store state with a session
    useTerminalStore.setState({
      sessions: {
        "session-1": {
          id: "session-1",
          ideId: "ide-1",
          title: "term 1",
          workdir: null,
          status: "connected",
          wsUrl: "ws://localhost/ws",
          wsTicket: "ticket",
          elevationExpiry: null,
          elevationMethod: null,
          elevationPrompt: false,
        },
      },
      activeSessionId: "session-1",
    })

    // Close the session
    useTerminalStore.getState().closeSession("session-1")

    expect(killTerminalSession).toHaveBeenCalledWith("session-1")

    // The store should synchronously update the session status to "closing"
    expect(useTerminalStore.getState().sessions["session-1"]).toBeDefined()
    expect(useTerminalStore.getState().sessions["session-1"].status).toBe("closing")

    // Wait for the microtasks to flush so the resolved promise is handled
    await new Promise((resolve) => setTimeout(resolve, 0))

    // The store should remove the session after successful kill
    expect(useTerminalStore.getState().sessions["session-1"]).toBeUndefined()
    expect(useTerminalStore.getState().activeSessionId).toBeNull()
  })
})
