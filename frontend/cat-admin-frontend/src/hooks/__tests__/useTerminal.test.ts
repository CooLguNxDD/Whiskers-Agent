import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useTerminalHosts } from "@/hooks/useTerminal"
import { getTerminalHostsWsTicket } from "@/api/terminal"

// Mock the API module
vi.mock("@/api/terminal", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/api/terminal")>()
  return {
    ...mod,
    getTerminalHostsWsTicket: vi.fn(),
  }
})

// Mock WebSocket implementation
class MockWebSocket {
  url: string
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()

  static instances: MockWebSocket[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }
}

describe("useTerminalHosts Hook", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.clearAllMocks()
    MockWebSocket.instances = []
    vi.stubGlobal("WebSocket", MockWebSocket)

    vi.mocked(getTerminalHostsWsTicket).mockResolvedValue({
      status: "ok",
      ws_url: "wss://example.com/terminal/hosts/ws",
      ws_ticket: "test-token",
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it("should call getTerminalHostsWsTicket and establish a WebSocket connection on mount", async () => {
    const { result } = renderHook(() => useTerminalHosts())

    // Initial state check
    expect(result.current.isPending).toBe(true)
    expect(result.current.data).toBeUndefined()

    // Wait for WebSocket connection attempt
    await waitFor(() => {
      expect(getTerminalHostsWsTicket).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(MockWebSocket.instances.length).toBe(1)
    })

    const ws = MockWebSocket.instances[0]
    expect(ws.url).toBe("wss://example.com/terminal/hosts/ws?token=test-token")
  })

  it("should update data when a message is received", async () => {
    const { result } = renderHook(() => useTerminalHosts())

    await waitFor(() => expect(MockWebSocket.instances.length).toBe(1))
    const ws = MockWebSocket.instances[0]

    // Simulate open
    act(() => {
      ws.onopen?.()
    })

    // Still pending, because no data is received yet
    expect(result.current.isPending).toBe(true)

    // Simulate receiving host snapshot
    const mockData = {
      status: "ok",
      hosts: [{ ide_id: "abc", since: 123 }],
    }
    act(() => {
      ws.onmessage?.({ data: JSON.stringify(mockData) })
    })

    // Data has arrived, no longer pending
    expect(result.current.isPending).toBe(false)
    expect(result.current.data).toEqual(mockData)
  })

  it("should reconnect on close after 5s", async () => {
    const { result } = renderHook(() => useTerminalHosts())

    await waitFor(() => expect(MockWebSocket.instances.length).toBe(1))
    const ws1 = MockWebSocket.instances[0]

    // Open connection
    act(() => {
      ws1.onopen?.()
    })

    // Load data
    const mockData = {
      status: "ok",
      hosts: [{ ide_id: "abc", since: 123 }],
    }
    act(() => {
      ws1.onmessage?.({ data: JSON.stringify(mockData) })
    })

    expect(result.current.isPending).toBe(false)
    expect(result.current.data).toEqual(mockData)

    // Simulate close
    act(() => {
      ws1.onclose?.()
    })

    // Should still have data, not pending
    expect(result.current.isPending).toBe(false)
    expect(result.current.data).toEqual(mockData)

    // Advance fake timer by 5 seconds to trigger reconnect
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })

    // Should call API and instantiate second WebSocket
    await waitFor(() => {
      expect(getTerminalHostsWsTicket).toHaveBeenCalledTimes(2)
    })
    await waitFor(() => {
      expect(MockWebSocket.instances.length).toBe(2)
    })

    const ws2 = MockWebSocket.instances[1]
    expect(ws2.url).toBe("wss://example.com/terminal/hosts/ws?token=test-token")
  })

  it("should close the WebSocket on unmount", async () => {
    const { unmount } = renderHook(() => useTerminalHosts())

    await waitFor(() => expect(MockWebSocket.instances.length).toBe(1))
    const ws = MockWebSocket.instances[0]

    unmount()

    expect(ws.close).toHaveBeenCalledTimes(1)
  })
})
