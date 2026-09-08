/**
 * Inline GOAP visualization for a single agent-mode chat message.
 * Renders the run_graph DAG + inspector panels from a per-message merged
 * state snapshot — live while the message is streaming, replay afterwards.
 * Supports a fullscreen overlay to avoid cramped inline rendering.
 */

import { useMemo, useState, useEffect, useRef } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { LogEntry, NodeId, SimState } from "./types"
import { buildSimState } from "./simDerive"
import { GraphCanvas } from "./GraphCanvas"
import { InspectorPanels } from "./InspectorPanels"
import { fetchPlaygroundGraph, type BackendGraph } from "../../api/playground"

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabIndex]:not([tabIndex="-1"])'

function getFocusableElements(root: HTMLElement | null): HTMLElement[] {
  if (!root) return []
  const candidates = Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR)) as HTMLElement[]
  // Exclude disabled controls and anything display:none/visibility:hidden —
  // offsetParent is null for both, except fixed-position elements, which
  // getClientRects() still catches.
  return candidates.filter(
    (el) => !el.hasAttribute("disabled") && (el.offsetParent !== null || el.getClientRects().length > 0),
  )
}

/**
 * Inline component for GOAP status display.
 */
export function GoapInline({
  goapState,
  log,
  running,
  onConfirm,
}: {
  goapState: Record<string, unknown>
  log: LogEntry[]
  running: boolean
  onConfirm?: (yes: boolean) => void
}) {
  const [selectedNode, setSelectedNode] = useState<NodeId | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [backendGraph, setBackendGraph] = useState<BackendGraph | undefined>()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const modalRef = useRef<HTMLDivElement>(null)
  const focusablesRef = useRef<HTMLElement[]>([])

  useEffect(() => {
    fetchPlaygroundGraph()
      .then(setBackendGraph)
      .catch((err) => console.warn("Failed to fetch backend graph topology:", err))
  }, [])

  useEffect(() => {
    if (!isFullscreen) return

    const currentTrigger = triggerRef.current
    const originalActive = document.activeElement as HTMLElement | null
    setTimeout(() => {
      closeButtonRef.current?.focus()
      focusablesRef.current = getFocusableElements(modalRef.current)
    }, 0)

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsFullscreen(false)
        return
      }

      if (e.key === "Tab") {
        const focusables = focusablesRef.current
        if (focusables.length === 0) return

        const first = focusables[0]
        const last = focusables[focusables.length - 1]

        if (e.shiftKey) {
          if (document.activeElement === first) {
            last.focus()
            e.preventDefault()
          }
        } else {
          if (document.activeElement === last) {
            first.focus()
            e.preventDefault()
          }
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown)

    return () => {
      window.removeEventListener("keydown", handleKeyDown)
      focusablesRef.current = []
      if (originalActive && typeof originalActive.focus === "function") {
        originalActive.focus()
      } else {
        currentTrigger?.focus()
      }
    }
  }, [isFullscreen])

  const sim = useMemo(() => buildSimState(goapState), [goapState])

  const state: SimState = {
    ...sim,
    query: "",
    scenarioId: null,
    running,
    log,
    selectedNode: selectedNode ?? sim.activeNode,
    retryCount: (goapState.retry_count as number) ?? 0,
    rawState: goapState,
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minHeight: 420 }}>
      {/* Inline view */}
      <div style={{ background: "var(--bg-sunken)", border: "1px solid var(--hairline)",
        borderRadius: "var(--radius)", overflow: "hidden", flexShrink: 0, height: 210,
        display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "6px 12px 0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9.5, letterSpacing: "0.14em",
            textTransform: "uppercase", color: "var(--fg-subtle)" }}>
            GRAPH · run_graph DAG
          </span>
          <button
            ref={triggerRef}
            type="button"
            className="ct-btn-ghost"
            style={{ fontSize: 10, padding: "2px 6px", height: "auto", display: "flex", alignItems: "center", gap: 4 }}
            onClick={() => setIsFullscreen(true)}
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
            </svg>
            Fullscreen Graph
          </button>
        </div>
        <GraphCanvas nodes={state.nodes} selectedNode={state.selectedNode} onSelectNode={setSelectedNode} backendGraph={backendGraph} rawState={state.rawState} />
      </div>

      <div style={{ height: 220, display: "flex" }}>
        <InspectorPanels state={state} onConfirm={onConfirm ?? (() => {})} />
      </div>

      {/* Fullscreen Overlay */}
      <AnimatePresence>
      {isFullscreen && (
        <motion.div
          ref={modalRef}
          role="dialog"
          aria-modal="true"
          aria-label="GOAP Execution Graph"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          style={{
            position: "fixed",
            top: 0, left: 0, right: 0, bottom: 0,
            background: "rgba(10, 10, 12, 0.88)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            zIndex: 99999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 24,
          }}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: 6 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 6 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            style={{
            background: "var(--card)",
            border: "1px solid var(--hairline)",
            borderRadius: 16,
            width: "100%",
            maxWidth: 1400,
            height: "90vh",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 10px 10px -5px rgba(0, 0, 0, 0.4)",
          }}>
            {/* Modal Header */}
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "16px 20px",
              borderBottom: "1px solid var(--hairline)",
              background: "var(--bg-sunken)",
            }}>
              <div>
                <h3 style={{ fontSize: 16, fontWeight: 600, color: "var(--fg)" }}>GOAP Execution Graph</h3>
                <p style={{ fontSize: 11, color: "var(--fg-muted)", marginTop: 2 }}>Visualizing run_graph DAG in real-time</p>
              </div>
              <button
                ref={closeButtonRef}
                type="button"
                className="ct-btn-ghost"
                style={{
                  width: 32, height: 32, padding: 0, borderRadius: "50%",
                  display: "flex", alignItems: "center", justifyItems: "center", justifyContent: "center"
                }}
                onClick={() => setIsFullscreen(false)}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>

            {/* Modal Body */}
            <div style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
              padding: 20,
              gap: 16,
            }}>
              {/* Spacious Canvas */}
              <div style={{
                background: "var(--bg-sunken)",
                border: "1px solid var(--hairline)",
                borderRadius: 12,
                overflow: "hidden",
                flex: "1 1 60%",
                display: "flex",
                flexDirection: "column",
                minHeight: 0
              }}>
                <div style={{ padding: "10px 14px 0", borderBottom: "1px solid var(--hairline)" }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.14em",
                    textTransform: "uppercase", color: "var(--fg-subtle)" }}>
                    DAG Canvas
                  </span>
                </div>
                <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, padding: 10, position: "relative" }}>
                  <GraphCanvas nodes={state.nodes} selectedNode={state.selectedNode} onSelectNode={setSelectedNode} backendGraph={backendGraph} rawState={state.rawState} />
                </div>
              </div>

              {/* Spacious Inspector Panels */}
              <div style={{
                flex: "0 0 35%",
                display: "flex",
                minHeight: 0,
                borderTop: "1px solid var(--hairline)",
                paddingTop: 12,
              }}>
                <InspectorPanels state={state} onConfirm={onConfirm ?? (() => {})} />
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  )
}