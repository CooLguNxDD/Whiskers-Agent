/**
 * useTerminal Hooks
 *
 * React hooks binding the UI to the real-time WebSocket IDE-hosts push telemetry.
 */

import { useEffect, useRef, useState } from "react"
import { getTerminalHostsWsTicket, type TerminalHostsResponse } from "@/api/terminal"

/**
 * Subscribe to the real-time WebSocket terminal hosts stream.
 */
function useTerminalHostsLive() {
  const [state, setState] = useState<{
    data: TerminalHostsResponse | null
    status: "connecting" | "connected" | "disconnected"
  }>({
    data: null,
    status: "disconnected",
  })

  // Ref (not a plain effect-local variable) so backoff survives a rapid
  // unmount/remount — e.g. React strict-mode's double-invoke — instead of
  // silently resetting to 0 and hammering the server at the fast retry rate.
  const reconnectAttemptRef = useRef(0)

  useEffect(() => {
    let ws: WebSocket | null = null
    let active = true
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null

    function getReconnectDelay(): number {
      // Exponential backoff: 1s, 2s, 4s, ... capped at 30s; counter capped at 10
      const attempt = reconnectAttemptRef.current
      const delay = Math.min(30000, 1000 * Math.pow(2, attempt))
      if (attempt < 10) reconnectAttemptRef.current = attempt + 1
      return delay
    }

    function scheduleReconnect() {
      if (!active) return
      const delay = getReconnectDelay()
      reconnectTimeout = setTimeout(connect, delay)
    }

    // Establish WebSocket connection using minted ticket
    async function connect() {
      if (!active) return
      setState((prev) => ({ ...prev, status: "connecting" }))
      try {
        const ticketRes = await getTerminalHostsWsTicket()
        if (!active) return

        const url = `${ticketRes.ws_url}?token=${encodeURIComponent(ticketRes.ws_ticket)}`
        ws = new WebSocket(url)

        ws.onopen = () => {
          if (!active) {
            ws?.close()
            return
          }
          reconnectAttemptRef.current = 0
          setState((prev) => ({ ...prev, status: "connected" }))
        }

        ws.onmessage = (ev) => {
          if (!active) return
          if (typeof ev.data === "string" && ev.data.startsWith("{")) {
            try {
              const parsed = JSON.parse(ev.data) as TerminalHostsResponse
              setState((prev) => ({ ...prev, data: parsed }))
            } catch (err) {
              console.error("Failed to parse live terminal hosts snapshot:", err)
            }
          }
        }

        ws.onclose = () => {
          // Drop the ref before reconnect so cleanup never double-closes a dead socket.
          ws = null
          if (!active) return
          setState((prev) => ({ ...prev, status: "disconnected" }))
          scheduleReconnect()
        }

        ws.onerror = () => {
          // Null ref before close so onclose/reconnect path does not reuse a dying socket.
          const dying = ws
          ws = null
          dying?.close()
        }
      } catch (err) {
        console.error("Failed to initiate live terminal connection:", err)
        setState((prev) => ({ ...prev, status: "disconnected" }))
        if (active) {
          scheduleReconnect()
        }
      }
    }

    connect()

    return () => {
      active = false
      if (ws) {
        ws.close()
      }
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout)
      }
    }
  }, [])

  return state
}

/** Query the operator's online IDE hosts (backed by live WebSocket updates). */
export function useTerminalHosts() {
  const { data } = useTerminalHostsLive()
  const isPending = !data
  return { data: data ?? undefined, isPending }
}
