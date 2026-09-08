/**
 * SessionSidebar Component
 *
 * Vertical console sessions sidebar replacing the old horizontal TabBar.
 * Manages active session tabs, rename triggers, closure actions, and elevation expiry timers.
 */

import { useState, useEffect, useMemo } from "react"
import { useTerminalStore } from "@/store/terminalSlice"
import { Plus } from "@/components/shell/Icons"

interface SessionSidebarProps {
  onNew: () => void
}

/** Helper to format remaining seconds as mm:ss. */
function formatRemaining(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, "0")}`
}

/**
 * Sidebar component for managing terminal sessions.
 */
export default function SessionSidebar({ onNew }: SessionSidebarProps) {
  const sessions = useTerminalStore((s) => s.sessions)
  const activeId = useTerminalStore((s) => s.activeSessionId)
  const setActive = useTerminalStore((s) => s.setActive)
  const closeSession = useTerminalStore((s) => s.closeSession)
  const renameSession = useTerminalStore((s) => s.renameSession)

  const [now, setNow] = useState(() => Date.now())
  const hasElevatedSession = useMemo(() => Object.values(sessions).some((s) => s.elevationExpiry !== null), [sessions])

  useEffect(() => {
    if (!hasElevatedSession) return

    const timer = setInterval(() => {
      const currentNow = Date.now()
      setNow(currentNow)

      const state = useTerminalStore.getState()
      Object.values(state.sessions).forEach((s) => {
        if (s.elevationExpiry !== null) {
          const remaining = Math.ceil(s.elevationExpiry - currentNow / 1000)
          if (remaining <= 0) {
            state.clearElevation(s.id)
          }
        }
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [hasElevatedSession])

  const list = useMemo(() => Object.values(sessions), [sessions])
  const activeSession = activeId ? sessions[activeId] : null

  function rename(id: string, current: string) {
    const next = window.prompt("Rename session", current)
    if (next && next.trim()) renameSession(id, next.trim())
  }

  return (
    <aside className="ts-aside">
      <div className="ts-aside-head">
        <span className="ct-eyebrow" style={{ color: "var(--term-dim)" }}>
          Sessions
        </span>
        <button className="ts-new-btn" onClick={onNew}>
          <Plus width="11" height="11" /> new
        </button>
      </div>

      <div className="ts-sess-list">
        {list.map((s) => {
          const isActive = s.id === activeId
          const remaining =
            s.elevationExpiry !== null ? Math.ceil(s.elevationExpiry - now / 1000) : 0

          let metaText = "disconnected"
          if (s.elevationExpiry !== null && remaining > 0) {
            metaText = `elevated · ⬆ ${formatRemaining(remaining)}`
          } else if (s.status === "connected") {
            metaText = "connected"
          } else if (s.status === "opening") {
            metaText = "opening…"
          } else if (s.status === "closing") {
            metaText = "closing…"
          } else if (s.status === "error") {
            metaText = "failed"
          }

          let dotClass = ""
          if (s.status === "opening" || s.status === "closing") {
            dotClass = " is-run"
          } else if (s.status === "connected" || s.status === "elevated") {
            dotClass = " is-ok"
          } else if (s.status === "error") {
            dotClass = " is-err"
          }

          return (
            <div
              key={s.id}
              role="button"
              tabIndex={0}
              className={"ts-sess" + (isActive ? " is-active" : "")}
              onClick={() => setActive(s.id)}
              onDoubleClick={() => rename(s.id, s.title)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  if (e.key === " ") {
                    e.preventDefault()
                  }
                  setActive(s.id)
                }
              }}
              title={s.error ?? s.title}
              style={{ display: "flex", width: "100%", border: "none", background: "none" }}
            >
              <span className={"ts-sess-dot" + dotClass} />
              <div className="ts-sess-body" style={{ flex: 1, minWidth: 0 }}>
                <div className="ts-sess-label">{s.title}</div>
                <div className="ts-sess-meta">{metaText}</div>
              </div>
              <button
                aria-label="close session"
                className="ts-sess-close"
                onClick={(e) => {
                  e.stopPropagation()
                  closeSession(s.id)
                }}
                onKeyDown={(e) => e.stopPropagation()}
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--fg-subtle)",
                  cursor: "pointer",
                  fontSize: "14px",
                  lineHeight: 1,
                  padding: "2px 6px",
                  alignSelf: "center",
                  opacity: 0.5,
                }}
              >
                ×
              </button>
            </div>
          )
        })}
      </div>

      {activeSession && (
        <div className="ts-aside-foot">
          <div className="ts-aside-row">
            <span className="ts-aside-key">IDE / Client</span>
            <span className="ts-aside-val ts-aside-accent">{activeSession.ideId}</span>
          </div>
          <div className="ts-aside-row">
            <span className="ts-aside-key">Working Dir</span>
            <span
              className="ts-aside-val"
              style={{
                maxWidth: 110,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={activeSession.workdir ?? "unknown"}
            >
              {activeSession.workdir ? activeSession.workdir.split(/[/\\]/).pop() : "default"}
            </span>
          </div>
        </div>
      )}
    </aside>
  )
}
