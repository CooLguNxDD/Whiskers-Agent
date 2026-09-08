/**
 * useLiveLogs Hook
 *
 * Manages the full SSE lifecycle for the LiveLogs terminal.
 *
 * Behaviour:
 * - awake && !paused  → stream is OPEN, entries flow into `logs`
 * - awake && paused   → stream stays OPEN, entries accumulate in `buffer`
 *                       (badge count visible so the user knows traffic is alive)
 * - !awake            → stream is CLOSED
 *
 * On resume (paused → false): buffer is flushed into `logs` and cleared.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import type { LogEntry, LogStreamHandle } from "@/api/logs"
import { streamServerLogs } from "@/api/logs"
import { useLogsStore } from "@/store"
import { bus } from "@/events/bus"

const MAX_DISPLAY = 100
let nextLogId = 1

export interface LiveLogsState {
  /** Entries currently shown in the terminal (filtered by display state). */
  logs: LogEntry[]
  /** Number of entries buffered while paused (badge count). */
  bufferedCount: number
  /** Whether the SSE connection is currently established. */
  isConnected: boolean
  /** Merge pause buffer into the display list (call from the resume click handler). */
  flushBuffer: () => void
}

/**
 * Hook to access live system logs.
 */
export function useLiveLogs(): LiveLogsState {
  const awake = useLogsStore((s) => s.awake)
  const paused = useLogsStore((s) => s.paused)

  const [logs, setLogs] = useState<LogEntry[]>([])
  const [buffer, setBuffer] = useState<LogEntry[]>([])
  const [isConnected, setIsConnected] = useState(false)

  const streamRef = useRef<LogStreamHandle | null>(null)
  const pausedRef = useRef(paused)
  const pendingLogsRef = useRef<LogEntry[]>([])
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    pausedRef.current = paused
  }, [paused])

  /** Flush buffered entries into the display list (call on resume click). */
  const flushBuffer = useCallback(() => {
    setBuffer((pending) => {
      if (pending.length > 0) {
        setLogs((prev) => [...pending, ...prev].slice(0, MAX_DISPLAY))
      }
      return []
    })
  }, [])

  // ── SSE lifecycle ─────────────────────────────────────────────────────────
  const handleEntry = useCallback((rawEntry: LogEntry) => {
    const entry = { ...rawEntry, id: rawEntry.id ?? nextLogId++ }
    pendingLogsRef.current.push(entry)

    // [Optimization]: Throttle state flush to prevent 60fps render thrashing
    // Preserves main-thread frame budget on high-throughput SSE streams.
    if (timerRef.current === null) {
      timerRef.current = window.setTimeout(() => {
        const batch = pendingLogsRef.current
        pendingLogsRef.current = []
        timerRef.current = null

        if (pausedRef.current) {
          setBuffer((prev) => [...[...batch].reverse(), ...prev].slice(0, MAX_DISPLAY))
        } else {
          setLogs((prev) => [...[...batch].reverse(), ...prev].slice(0, MAX_DISPLAY))
        }
      }, 150) // ~6 FPS max update rate
    }
  }, [])

  /** Drain any pending throttled batch into state instead of losing it to a cleared timer. */
  const flushPendingTimer = useCallback(() => {
    if (timerRef.current === null) return
    clearTimeout(timerRef.current)
    timerRef.current = null
    const batch = pendingLogsRef.current
    pendingLogsRef.current = []
    if (batch.length === 0) return
    if (pausedRef.current) {
      setBuffer((prev) => [...[...batch].reverse(), ...prev].slice(0, MAX_DISPLAY))
    } else {
      setLogs((prev) => [...[...batch].reverse(), ...prev].slice(0, MAX_DISPLAY))
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    if (!awake) {
      // Tear down the stream
      streamRef.current?.close()
      streamRef.current = null
      const timer = setTimeout(() => {
        if (cancelled) return
        setIsConnected(false)
        setLogs([])
        setBuffer([])
      }, 0)
      bus.emit("logs:disconnected", undefined)
      return () => {
        cancelled = true
        clearTimeout(timer)
        flushPendingTimer()
      }
    }

    // Open the stream
    const initTimer = setTimeout(() => {
      if (!cancelled) setIsConnected(false)
    }, 0)

    const handle = streamServerLogs(
      (entry) => {
        if (cancelled) return
        setIsConnected(true)
        handleEntry(entry)
      },
      () => {
        if (cancelled) return
        setIsConnected(false)
        bus.emit("logs:disconnected", undefined)
      },
    )

    // Mark connected optimistically once stream is created
    const connectTimer = setTimeout(() => {
      if (cancelled) return
      setIsConnected(true)
      bus.emit("logs:connected", undefined)
    }, 0)
    
    streamRef.current = handle

    return () => {
      cancelled = true
      handle.close()
      streamRef.current = null
      clearTimeout(initTimer)
      clearTimeout(connectTimer)
      setIsConnected(false)
      // Drain the pending throttle batch before tearing down (or toggling
      // `awake` off) so the last <150ms of entries aren't stranded.
      flushPendingTimer()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awake])

  return {
    logs,
    bufferedCount: buffer.length,
    isConnected,
    flushBuffer,
  }
}
