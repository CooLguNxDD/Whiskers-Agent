/**
 * TerminalPanel Component
 *
 * The /terminal page body: tab bar + the stack of hidden-mounted <TerminalView>s +
 * the new-session dialog. All open sessions stay mounted (only the active one is
 * visible) so switching tabs never tears down a host PTY.
 */

import { useState, memo } from "react"
import { useShallow } from "zustand/react/shallow"
import { useTerminalStore } from "@/store/terminalSlice"
import SessionSidebar from "./SessionSidebar"
import TerminalView from "./TerminalView"
import NewSessionDialog from "./NewSessionDialog"
import ElevationPrompt from "./ElevationPrompt"
import HostTokenButton from "./HostTokenButton"

interface TerminalViewWrapperProps {
  id: string
  active: boolean
}

const TerminalViewWrapper = memo(function TerminalViewWrapper({ id, active }: TerminalViewWrapperProps) {
  const session = useTerminalStore((s) => s.sessions[id])
  if (!session) return null
  return <TerminalView session={session} active={active} />
})

/**
 * Renders the multi-session terminal manager panel.
 */
export default function TerminalPanel() {
  const sessionIds = useTerminalStore(
    useShallow((s) => Object.keys(s.sessions))
  )
  const activeId = useTerminalStore((s) => s.activeSessionId)
  const [dialogOpen, setDialogOpen] = useState(false)

  const activeSession = useTerminalStore((s) => activeId ? s.sessions[activeId] : null)

  return (
    <div className="ts-layout">
      <SessionSidebar onNew={() => setDialogOpen(true)} />

      <div className="ts-term">
        <div className="ts-term-head">
          <span className="ts-hd-prompt">&gt;_</span>
          <span className="ts-hd-title">
            {activeSession ? activeSession.title : "Console Sessions"}
          </span>
          <div className="ts-hd-right">
            <HostTokenButton />
          </div>
        </div>

        <div className="ts-body" style={{ flex: 1, minHeight: 0, position: "relative" }}>
          {sessionIds.length === 0 ? (
            <div
              style={{
                height: "100%",
                display: "grid",
                placeItems: "center",
                color: "var(--fg-muted)",
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                border: "1px dashed var(--border)",
                borderRadius: 8,
              }}
            >
              No terminals open. Click <span style={{ color: "var(--term-green)", margin: "0 4px" }}>new</span> to spawn one.
            </div>
          ) : (
            <div style={{ position: "absolute", inset: 0 }}>
              {sessionIds.map((id) => (
                <div key={id} style={{ position: "absolute", inset: 0 }}>
                  <TerminalViewWrapper id={id} active={id === activeId} />
                </div>
              ))}
            </div>
          )}

          {activeSession?.elevationPrompt && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "grid",
                placeItems: "center",
                backgroundColor: "color-mix(in oklch, var(--term-bg, #0c0f0d) 75%, transparent)",
                zIndex: 10,
                borderRadius: 8,
              }}
            >
              <ElevationPrompt session={activeSession} />
            </div>
          )}
        </div>
      </div>

      <NewSessionDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </div>
  )
}
