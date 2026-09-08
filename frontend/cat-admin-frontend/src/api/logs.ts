/**
 * Server Log Streaming API
 *
 * Opens an SSE connection to the logs stream and delivers structured log
 * entries in real time. Path resolved via OperationCatalog when available.
 */

import { getErrorMessage } from "@/utils/errors"
import { tryRefreshSession } from "./client"
import { resolveCatalogPath } from "./catalogRuntime"

export interface LogEntry {
  id?: number  // Unique identifier for stable React rendering keys
  t: string    // "HH:MM:SS"
  tag: string  // "SYS" | "REQ" | " OK" | "ERR" | "WARN" | "DBG"
  cls: string  // "sys" | "req" | "ok" | "err" | "warn" | "dbg"
  msg: string
  level: string // Python levelname: "INFO" | "WARNING" | "ERROR" | ...
}

export interface LogStreamHandle {
  close: () => void
}

/**
 * Open a live SSE stream to /api/logs/stream.
 *
 * @param onEntry  Called for each parsed log entry.
 * @param onError  Called when the stream encounters a fatal error.
 * @returns        A handle with a `close()` method for cleanup.
 */
export function streamServerLogs(
  onEntry: (entry: LogEntry) => void,
  onError?: (err: Error) => void,
): LogStreamHandle {
  let abortController = new AbortController()
  let closed = false
  let retryCount = 0
  const MAX_RETRIES = 5
  const BASE_DELAY = 1000 // 1s
  const MAX_DELAY = 30000 // 30s
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  async function connect(retryAuth = false): Promise<void> {
    if (closed) return

    try {
      const resolved = await resolveCatalogPath("api.log", "api_logs_stream")
      const path = resolved?.path ?? "/api/logs/session_gated/stream"
      const res = await fetch(path, {
        signal: abortController.signal,
        credentials: "include",
        headers: { Accept: "text/event-stream" },
      })

      if (res.status === 401 && !retryAuth) {
        const refreshed = await tryRefreshSession()
        if (refreshed && !closed) {
          return connect(true)
        }
        onError?.(new Error("Unauthorized"))
        return
      }

      if (!res.ok) {
        throw new Error(`Log stream failed: ${res.status}`)
      }

      const reader = res.body?.getReader()
      if (!reader) {
        throw new Error("No response body")
      }

      // Reset retry count on successful connection
      retryCount = 0

      const decoder = new TextDecoder()
      let buffer = ""

      try {
        while (!closed) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          if (buffer.length > 1024 * 1024) {
            throw new Error("Log stream buffer overflow limit exceeded")
          }
          const frames = buffer.split("\n\n")
          buffer = frames.pop() ?? ""

          for (const frame of frames) {
            const line = frame.trim()
            if (!line || line.startsWith(":")) continue // heartbeat comment
            if (line.startsWith("data: ")) {
              try {
                const entry = JSON.parse(line.slice(6)) as LogEntry
                onEntry(entry)
              } catch {
                // Malformed frame — skip
              }
            }
          }
        }
      } finally {
        reader.cancel().catch(() => {})
      }
    } catch (err) {
      if (closed || (err instanceof Error && err.name === "AbortError")) {
        return
      }

      // SSE connection disconnected/failed -> trigger retry with exponential backoff + jitter
      if (retryCount < MAX_RETRIES) {
        const delay = Math.min(MAX_DELAY, BASE_DELAY * Math.pow(2, retryCount))
        const jitter = Math.random() * 500 // jitter up to 500ms
        const finalDelay = delay + jitter

        console.warn(`Log stream connection failed. Reconnecting in ${Math.round(finalDelay)}ms... (Attempt ${retryCount + 1}/${MAX_RETRIES})`, err)
        retryCount++

        reconnectTimer = setTimeout(() => {
          if (!closed) {
            // Re-create AbortController so the new fetch works
            abortController = new AbortController()
            connect()
          }
        }, finalDelay)
      } else {
        console.error("Max retries reached for log stream connection.", err)
        onError?.(err instanceof Error ? err : new Error(getErrorMessage(err)))
      }
    }
  }

  void connect()

  return {
    close() {
      closed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      abortController.abort()
    },
  }
}
