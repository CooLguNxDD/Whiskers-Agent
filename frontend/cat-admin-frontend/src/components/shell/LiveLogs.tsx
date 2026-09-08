/**
 * LiveLogs Component
 *
 * Real-time terminal that streams live server logs from the backend via SSE.
 * State is fully owned by Zustand (useLogsStore) following the react-app-guide pattern:
 *
 * - `awake` → persisted to localStorage; the "Awake spell" button controls it.
 * - `paused` → transient; freeze display without closing the stream.
 * - `filter` → transient text filter applied to displayed rows.
 * - `tagFilter` → transient chip filter (ALL | SYS | REQ | OK | ERR | WARN).
 *
 * The SSE stream is managed by `useLiveLogs()` which:
 * - Opens the connection when awake, closes it when asleep.
 * - Buffers entries while paused and flushes them on resume.
 */

import { useRef, useEffect, useMemo, memo } from "react"
import { Paw, Pause, Play, Filter } from "./Icons"
import { useLogsStore } from "@/store"
import { useLiveLogs } from "@/hooks/useLiveLogs"
import type { LogEntry } from "@/api/logs"

const TAG_CHIPS = [
  { label: "ALL",  value: "",     cls: "" },
  { label: "SYS",  value: "sys",  cls: "sys" },
  { label: "REQ",  value: "req",  cls: "req" },
  { label: "OK",   value: "ok",   cls: "ok" },
  { label: "ERR",  value: "err",  cls: "err" },
  { label: "WARN", value: "warn", cls: "warn" },
]

function matchesFilter(entry: LogEntry, text: string, tag: string): boolean {
  if (tag && entry.cls !== tag) return false
  if (!text) return true
  const q = text.toLowerCase()
  return entry.msg.toLowerCase().includes(q) || entry.tag.toLowerCase().includes(q)
}

const MemoizedLogRow = memo(function MemoizedLogRow({ l }: { l: LogEntry }) {
  return (
    <div className="ct-log-row">
      <span className="ct-log-t">[{l.t}]</span>
      <span className={"ct-log-tag " + l.cls}>{l.tag}:</span>
      <span className="ct-log-msg">{l.msg}</span>
    </div>
  )
})

/**
 * Renders the real-time log terminal at the bottom of the shell.
 */
export default function LiveLogs() {
  const awake     = useLogsStore((s) => s.awake)
  const paused    = useLogsStore((s) => s.paused)
  const filter    = useLogsStore((s) => s.filter)
  const tagFilter = useLogsStore((s) => s.tagFilter)

  const setAwake     = useLogsStore((s) => s.setAwake)
  const setPaused    = useLogsStore((s) => s.setPaused)
  const setFilter    = useLogsStore((s) => s.setFilter)
  const setTagFilter = useLogsStore((s) => s.setTagFilter)

  const { logs, bufferedCount, isConnected, flushBuffer } = useLiveLogs()

  // Auto-scroll to top (newest entries are prepended)
  const bodyRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!paused && bodyRef.current) {
      bodyRef.current.scrollTop = 0
    }
  }, [logs, paused])

  const displayed = useMemo(() => logs.filter((e) => matchesFilter(e, filter, tagFilter)), [logs, filter, tagFilter])

  return (
    <div className={"ct-term" + (awake ? "" : " is-closed")}>
      <div className="ct-term-head">
        <span style={{ color: "var(--term-green)", textShadow: "0 0 8px var(--term-green)" }}>&gt;_</span>
        <span className="ct-term-title">Live Traffic</span>
        <span style={{ color: "var(--term-dim)" }}>· last 5m</span>

        <div className="right">
          {/* Connection pulse */}
          <span className={"ct-term-pulse" + (isConnected && awake ? "" : " is-off")}>
            {awake ? (isConnected ? "Listening" : "Connecting…") : "Napping"}
          </span>

          {/* Pause / Resume — only shown when awake */}
          {awake && (
            <button
              className={"ct-term-pause-btn" + (paused ? " is-paused" : "")}
              aria-pressed={paused}
              onClick={() => {
                if (paused) flushBuffer()
                setPaused(!paused)
              }}
              title={paused ? `Resume (${bufferedCount} buffered)` : "Pause stream"}
            >
              {paused ? <Play width={11} height={11} /> : <Pause width={11} height={11} />}
              {paused ? (
                <>
                  Resume
                  {bufferedCount > 0 && (
                    <span className="ct-term-badge">{bufferedCount}</span>
                  )}
                </>
              ) : "Pause"}
            </button>
          )}

          {/* Awake / Asleep spell toggle */}
          <button
            className={"ct-paw-toggle" + (awake ? "" : " is-off")}
            aria-pressed={awake}
            onClick={() => {
              setAwake(!awake)
              // Reset pause when waking up so it's a clean start
              if (!awake) setPaused(false)
            }}
            title={awake ? "Let the cat nap (stop listening)" : "Wake the cat (start listening)"}
          >
            <span className="paw-ico"><Paw /></span>
            {awake ? "Awake" : "Asleep"}
          </button>
        </div>
      </div>

      {/* Filter row — only visible when awake */}
      {awake && (
        <div className="ct-term-filter">
          <span className="ct-term-filter-icon"><Filter width={11} height={11} /></span>
          <input
            className="ct-term-filter-input"
            placeholder="filter logs…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            spellCheck={false}
          />
          <div className="ct-term-chips">
            {TAG_CHIPS.map((chip) => (
              <button
                key={chip.value}
                className={
                  "ct-term-chip" +
                  (chip.cls ? ` ${chip.cls}` : "") +
                  (tagFilter === chip.value ? " is-active" : "")
                }
                aria-pressed={tagFilter === chip.value}
                onClick={() => setTagFilter(chip.value)}
              >
                {chip.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {awake ? (
        <div className="ct-term-body" ref={bodyRef}>
          {displayed.length === 0 && (
            <div className="ct-log-row" style={{ color: "var(--term-dim)" }}>
              <span className="ct-log-t">[{new Date().toTimeString().slice(0, 8)}]</span>
              <span className="ct-log-msg">
                {filter || tagFilter
                  ? "No log entries match the current filter."
                  : "Waiting for log entries…"}
              </span>
            </div>
          )}
          {displayed.map((l) => (
            <MemoizedLogRow key={l.id} l={l} />
          ))}
          <div className="ct-log-row">
            <span className="ct-log-t">[{new Date().toTimeString().slice(0, 8)}]</span>
            <span className="ct-log-cursor" />
          </div>
        </div>
      ) : (
        <div className="ct-term-sleep">
          <span className="zs">z</span>
          <span className="zs" style={{ fontSize: 16, opacity: 0.6 }}>z</span>
          <span className="zs" style={{ fontSize: 12, opacity: 0.35 }}>z</span>
          &nbsp;cat is napping
        </div>
      )}
    </div>
  )
}
