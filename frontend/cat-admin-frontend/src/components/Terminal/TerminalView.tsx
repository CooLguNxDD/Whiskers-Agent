/**
 * TerminalView Component
 *
 * Owns one Xterm.js instance bound to a single relay session. Connects the console
 * WebSocket leg (`ws_url?token=ws_ticket`) and pumps bytes both ways (dumb pipe;
 * the host extension owns the command guard). Inactive tabs stay mounted but hidden
 * (display:none) so the host PTY survives tab switches — disposal happens only when
 * the session is removed from the store (explicit close).
 */

import { useEffect, useRef } from "react"
import { Terminal } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import "@xterm/xterm/css/xterm.css"
import { useTerminalStore, type TerminalSession } from "@/store/terminalSlice"
import { usePreferencesStore } from "@/store"

interface TerminalViewProps {
  session: TerminalSession
  active: boolean
}

/** Reads the current `--term-*` CSS custom properties into an xterm theme object. */
function readXtermTheme() {
  const cssVars = getComputedStyle(document.documentElement)
  const v = (name: string, fallback: string) => cssVars.getPropertyValue(name).trim() || fallback
  return {
    background:          v('--term-bg', '#0c0f0d'),
    foreground:          v('--term-fg', '#e8e6df'),
    cursor:              v('--term-amber', '#f0b35b'),
    selectionBackground: v('--bg-elevated', '#3a3326'),
    black:               v('--term-dim', '#5c5648'),
    green:               v('--term-green', '#8fbf6b'),
    magenta:             v('--term-pink', '#e98fc2'),
    cyan:                v('--term-cyan', '#6bc7d6'),
  }
}

/**
 * Renders a live terminal pane bound to one relay session.
 */
export default function TerminalView({ session, active }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const isDisposedRef = useRef<boolean>(false)
  const setStatus = useTerminalStore((s) => s.setStatus)
  const themeId = usePreferencesStore((s) => s.theme)

  const ticketRef = useRef(session.wsTicket)
  useEffect(() => {
    ticketRef.current = session.wsTicket
  }, [session.wsTicket])

  // Mount Xterm + WebSocket once per session (id is stable for the view's life).
  useEffect(() => {
    isDisposedRef.current = false
    const xtermTheme = readXtermTheme()

    const term = new Terminal({
      fontFamily: '"JetBrains Mono", "Geist Mono", monospace',
      fontSize: 13,
      cursorBlink: true,
      theme: xtermTheme,
      convertEol: false,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    if (containerRef.current) {
      term.open(containerRef.current)
      try { fit.fit() } catch { /* container not laid out yet */ }
    }
    termRef.current = term
    fitRef.current = fit

    const url = `${session.wsUrl}?token=${encodeURIComponent(ticketRef.current)}`
    const ws = new WebSocket(url)
    ws.binaryType = "arraybuffer"

    ws.onopen = () => setStatus(session.id, "connected")
    ws.onmessage = (ev) => {
      if (isDisposedRef.current) return
      if (typeof ev.data === "string") {
        // Fast-path prefix check to avoid slow V8 exception generation from JSON.parse() on raw terminal strings
        if (ev.data.startsWith("{")) {
          try {
            const ctrl = JSON.parse(ev.data)
            if (ctrl && ctrl.t === "elevation_required") {
              useTerminalStore.getState().requestElevation(session.id, ctrl.method ?? "totp")
              return
            }
          } catch { /* not a control frame — fall through to terminal */ }
        }
        try { term.write(ev.data) } catch { /* ignore write after dispose */ }
      } else {
        try { term.write(new Uint8Array(ev.data as ArrayBuffer)) } catch { /* ignore write after dispose */ }
      }
    }
    ws.onclose = (ev) => {
      if (ev.code === 4401) setStatus(session.id, "error", "unauthorized (4401)")
      else if (ev.code === 4403) setStatus(session.id, "error", "forbidden (4403)")
      else setStatus(session.id, "closed")
    }
    ws.onerror = () => setStatus(session.id, "error", "connection error")

    const onData = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data)
    })

    return () => {
      isDisposedRef.current = true
      onData.dispose()
      try { ws.close() } catch { /* already closed */ }
      try { term.dispose() } catch { /* already disposed */ }
      termRef.current = null
      fitRef.current = null
    }
    // session.id is stable; wsUrl/wsTicket captured at first mount intentionally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id])

  // Re-apply the xterm theme when the site theme changes. Deferred to a rAF since
  // ThemeProvider (an ancestor) writes the --term-* CSS vars in its own effect, and
  // child effects commit before parent effects on the same update.
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      if (termRef.current) termRef.current.options.theme = readXtermTheme()
    })
    return () => cancelAnimationFrame(id)
  }, [themeId])

  // Refit when this tab becomes active or the container resizes.
  useEffect(() => {
    if (!active) return
    const refit = () => {
      if (isDisposedRef.current) return
      try { fitRef.current?.fit() } catch { /* not laid out */ }
    }
    refit()
    // Throttle fit() to the display refresh rate to avoid layout thrashing on resize.
    let frameId = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frameId)
      frameId = requestAnimationFrame(refit)
    })
    if (containerRef.current) ro.observe(containerRef.current)
    return () => {
      cancelAnimationFrame(frameId)
      ro.disconnect()
    }
  }, [active])

  return (
    <div
      ref={containerRef}
      style={{
        display: active ? "block" : "none",
        width: "100%",
        height: "100%",
        padding: 8,
        background: "var(--term-bg, #0c0f0d)",
        borderRadius: 8,
      }}
    />
  )
}
