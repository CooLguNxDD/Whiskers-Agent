import { createFileRoute } from "@tanstack/react-router"
import AppShell from "@/components/shell/AppShell"
import TerminalPanel from "@/components/Terminal/TerminalPanel"
import { useAuthState } from "@/store"

/**
 * Terminal route configuration.
 */
export const Route = createFileRoute("/terminal")({
  component: function TerminalPage() {
    const { status } = useAuthState()

    return (
      <AppShell active="terminal">
        {status !== "connected" ? (
          <>
            <div className="ct-page-head">
              <div>
                <div className="ct-page-title">
                  Terminal
                  <span className="ct-eyebrow">/ remote shell</span>
                </div>
                <div className="ct-page-sub">
                  Drive a real interactive shell hosted in your IDE, relayed through Whiskers Agent.
                </div>
              </div>
            </div>
            <div style={{ color: "var(--fg-muted)", fontFamily: "var(--font-mono)", fontSize: 13, padding: 20 }}>
              Sign in to open a terminal session.
            </div>
          </>
        ) : (
          <TerminalPanel />
        )}
      </AppShell>
    )
  },
})
