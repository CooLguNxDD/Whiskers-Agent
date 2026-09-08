import React, { useState, useEffect, useRef } from "react"
import type { NodeId, SimState, LogEntry, SimNodeState } from "./types"
import { NODE_REGISTRY } from "./NodeRegistry"
import { SessionMemoryView } from "./NodeOutputs"
import { auditByNode, roleHintForNode, type ModelAuditEntry } from "./modelAudit"

const nodeMap = Object.fromEntries(NODE_REGISTRY.map(n => [n.id, n]))

const YamlLine = React.memo(function YamlLine({ line }: { line: string }) {
  const trim = line.trim()
  if (trim.startsWith("#")) {
    return <div style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>{line}</div>
  }
  const kv = line.match(/^(\s*)([-]?\s*)([\w_-]+):\s*(.*)$/)
  if (kv) {
    const [, indent, dash, key, raw] = kv
    let valNode: React.ReactNode = null
    if (raw !== "") {
      if (raw.startsWith('"') || raw.startsWith("'")) {
        valNode = <span style={{ color: "var(--neon)" }}>{raw}</span>
      } else if (raw.startsWith("$")) {
        valNode = <span style={{ color: "var(--cyan)" }}>{raw}</span>
      } else if (raw.startsWith("[") || raw.startsWith("{")) {
        valNode = <span style={{ color: "var(--amber)" }}>{raw}</span>
      } else if (raw.includes("#")) {
        const [val, comment] = raw.split("#")
        valNode = (
          <>
            <span style={{ color: "var(--fg-muted)" }}>{val}</span>
            <span style={{ color: "var(--fg-subtle)", fontStyle: "italic" }}>#{comment}</span>
          </>
        )
      } else {
        valNode = <span style={{ color: "var(--fg-muted)" }}>{raw}</span>
      }
    }
    return (
      <div>
        <span style={{ color: "var(--fg-subtle)" }}>{indent}</span>
        {dash && <span style={{ color: "var(--pink)" }}>{dash}</span>}
        <span style={{ color: "var(--amber)" }}>{key}</span>
        <span style={{ color: "var(--border-strong)" }}>:</span>
        {valNode !== null && <span> {valNode}</span>}
      </div>
    )
  }
  return <div style={{ color: "var(--fg-subtle)" }}>{line}</div>
})

function YamlView({ yaml }: { yaml: string | null }) {
  if (!yaml) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", height: "100%", gap: 8, color: "var(--fg-subtle)" }}>
        <svg width={28} height={28} viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth={1.4} strokeLinecap="round">
          <rect x={3} y={3} width={18} height={18} rx={3} />
          <path d="M7 8h10M7 12h10M7 16h6" />
        </svg>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em" }}>
          YAML AVAILABLE AFTER BUILDER NODE
        </span>
      </div>
    )
  }

  const duplicateTracker: Record<string, number> = {}
  const yamlLines = yaml.split("\n").map((line) => {
    duplicateTracker[line] = (duplicateTracker[line] ?? 0) + 1
    return {
      line,
      key: `${line}__${duplicateTracker[line]}`,
    }
  })

  return (
    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.7,
      whiteSpace: "pre", overflowX: "auto", padding: "14px 16px" }}>
      {yamlLines.map((item) => (
        <YamlLine key={item.key} line={item.line} />
      ))}
    </div>
  )
}

const LOG_COLORS: Record<string, string> = {
  green:   "var(--term-green)",
  amber:   "var(--term-amber)",
  pink:    "var(--term-pink)",
  cyan:    "var(--term-cyan)",
  dim:     "var(--term-dim)",
  default: "var(--term-fg)",
}

const LogTerminalRow = React.memo(function LogTerminalRow({ entry }: { entry: LogEntry }) {
  return (
    <div style={{ display: "flex", gap: 10 }}>
      <span style={{ color: "var(--term-dim)", flexShrink: 0, userSelect: "none" }}>{entry.ts}</span>
      <span style={{ color: LOG_COLORS[entry.color] || LOG_COLORS.default }}>{entry.msg}</span>
    </div>
  )
})

function LogTerminal({ log }: { log: LogEntry[] }) {
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (bottomRef.current?.parentElement) {
      bottomRef.current.parentElement.scrollTop = bottomRef.current.offsetTop
    }
  }, [log.length])

  return (
    <div style={{ background: "var(--term-bg)", height: "100%", overflowY: "auto",
      padding: "12px 14px", fontFamily: "var(--font-mono)", fontSize: 11.5, lineHeight: 1.75 }}>
      {log.length === 0 ? (
        <div style={{ color: "var(--term-dim)", display: "flex", alignItems: "center", gap: 8, paddingTop: 4 }}>
          <span style={{ color: "var(--term-amber)" }}>&gt;_</span>
          <span>run_graph · awaiting query…</span>
        </div>
      ) : (
        log.map(entry => (
          <LogTerminalRow key={entry.id} entry={entry} />
        ))
      )}
      <div ref={bottomRef} />
    </div>
  )
}

function ModelAuditBlock({ audit }: { audit: ModelAuditEntry[] }) {
  return (
    <div style={{ marginBottom: 14, padding: "8px 10px", borderRadius: 6,
      border: "1px solid var(--hairline)", background: "var(--bg-sunken)" }}>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em",
        color: "var(--fg-subtle)", marginBottom: 6, textTransform: "uppercase" }}>Model</div>
      {audit.map((entry, i) => (
        <div key={i} style={{ marginBottom: i < audit.length - 1 ? 8 : 0, fontFamily: "var(--font-mono)", fontSize: 11 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <span style={{ color: "var(--fg-muted)" }}>{entry.role}</span>
            <span style={{ color: "var(--fg-subtle)" }}>rung {entry.rung_index}</span>
            <span style={{ color: entry.escalated ? "var(--amber)" : "var(--ok)" }}>{entry.model}</span>
            <span style={{
              color: entry.status === "ok" ? "var(--ok)" : entry.status === "exhausted" ? "var(--danger)" : "var(--warn)",
            }}>{entry.status}</span>
            {entry.escalated && <span style={{ color: "var(--amber)" }}>escalated</span>}
            <span style={{ color: "var(--fg-subtle)" }}>{entry.elapsed_ms}ms</span>
          </div>
          {entry.reason && (
            <div style={{ color: "var(--fg-subtle)", fontSize: 10, marginTop: 2 }}>{entry.reason}</div>
          )}
          {entry.attempts?.length > 0 && (
            <div style={{ marginTop: 4, paddingLeft: 10 }}>
              {entry.attempts.map((a, j) => (
                <div key={j} style={{ color: "var(--fg-subtle)", fontSize: 10 }}>
                  #{a.rung_index} {a.selector} → {a.model} [{a.status}]{a.reason ? ` — ${a.reason}` : ""} ({a.elapsed_ms}ms)
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function NodeInspector({ nodeId, nodeState, rawState }: {
  nodeId: NodeId | null; nodeState?: SimNodeState; rawState?: Record<string, unknown>
}) {
  if (!nodeId) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
        height: "100%", gap: 8, color: "var(--fg-subtle)",
        fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em" }}>
        SELECT A NODE TO INSPECT
      </div>
    )
  }
  const ns = nodeState ?? { status: "idle" as const, output: null }
  const nodeAudit = auditByNode(rawState)[nodeId]
  const hasModelRole = roleHintForNode(nodeId).length > 0
  const out = ns.output as Record<string, unknown> | null
  const statusColor: Record<string, string> = {
    idle: "var(--fg-subtle)", active: "var(--amber)", done: "var(--ok)",
    error: "var(--danger)", skipped: "var(--fg-subtle)", waiting: "var(--warn)",
  }
  const sc = statusColor[ns.status] || "var(--fg-subtle)"

  return (
    <div style={{ padding: "12px 16px", overflowY: "auto", height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: sc,
          boxShadow: ns.status === "active" ? `0 0 8px ${sc}` : "none" }} />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600,
          color: "var(--fg)", letterSpacing: "0.03em" }}>{nodeId}</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.12em",
          color: sc, textTransform: "uppercase", marginLeft: 4 }}>{ns.status}</span>
      </div>
      {ns.status === "idle"    && <p style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>awaiting execution…</p>}
      {ns.status === "skipped" && <p style={{ fontSize: 12, color: "var(--fg-subtle)", fontFamily: "var(--font-mono)" }}>skipped — not on active path</p>}
      {ns.status === "active"  && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--amber)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
          <span>processing</span><span className="ct-blink">▌</span>
        </div>
      )}
      {nodeAudit && nodeAudit.length > 0 && <ModelAuditBlock audit={nodeAudit} />}
      {!nodeAudit && hasModelRole && ns.status !== "idle" && (
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--fg-subtle)", marginBottom: 10 }}>
          model: core (MODEL_ROLES_ENABLED off, or no audit recorded)
        </div>
      )}
      {out && nodeMap[nodeId]?.render(out)}
    </div>
  )
}

/**
 * Panels for inspecting GOAP nodes and edges.
 */
export function InspectorPanels({ state, onConfirm }: { state: SimState; onConfirm: (yes: boolean) => void }) {
  const [tab, setTab] = useState<"inspector" | "yaml" | "log" | "memory">("inspector")
  const tabs = [
    { id: "inspector" as const, label: "NODE OUTPUT" },
    { id: "yaml"      as const, label: "YAML PLAN" },
    { id: "log"       as const, label: "EXEC LOG" },
    { id: "memory"    as const, label: "SESSION MEMORY" },
  ]
  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0,
      border: "1px solid var(--hairline)", borderRadius: "var(--radius)",
      background: "var(--card)", overflow: "hidden" }}>
      <div role="tablist" style={{ display: "flex", alignItems: "center", gap: 2,
        borderBottom: "1px solid var(--hairline)", padding: "0 12px", flexShrink: 0 }}>
        {tabs.map(t => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} id={`tab-${t.id}`} aria-controls={`panel-${t.id}`} onClick={() => setTab(t.id)} style={{
            padding: "9px 14px", fontFamily: "var(--font-mono)", fontSize: 10, fontWeight: 600,
            letterSpacing: "0.14em", textTransform: "uppercase",
            color: tab === t.id ? "var(--amber)" : "var(--fg-subtle)",
            background: "none", border: "none", cursor: "pointer",
            borderBottom: `2px solid ${tab === t.id ? "var(--amber)" : "transparent"}`,
            marginBottom: -1, transition: "color 120ms",
          }}>
            {t.label}
          </button>
        ))}

        {state.awaiting === "confirm" && (
          <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            <button onClick={() => onConfirm(false)} style={{
              padding: "5px 14px", borderRadius: 6, background: "var(--bg-sunken)",
              border: "1px solid var(--border)", color: "var(--fg-muted)",
              fontFamily: "var(--font-mono)", fontSize: 11, cursor: "pointer" }}>
              Cancel
            </button>
            <button onClick={() => onConfirm(true)} style={{
              padding: "5px 14px", borderRadius: 6,
              background: "linear-gradient(135deg, var(--amber-glow), var(--amber))",
              border: "none", color: "oklch(0.18 0.040 40)",
              fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700,
              cursor: "pointer", boxShadow: "var(--glow-amber)" }}>
              ✓ Confirm &amp; Execute
            </button>
          </div>
        )}
        {state.awaiting === "clarify" && (
          <div style={{ marginLeft: "auto" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--pink)", letterSpacing: "0.1em" }}>
              ↺ TYPE A NEW QUERY TO RETRY
            </span>
          </div>
        )}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        {tab === "inspector" && (
          <NodeInspector
            nodeId={state.selectedNode}
            nodeState={state.selectedNode ? state.nodes[state.selectedNode] : undefined}
            rawState={state.rawState}
          />
        )}
        {tab === "yaml" && (
          <div style={{ height: "100%", overflowY: "auto", background: "var(--bg-sunken)" }}>
            <YamlView yaml={state.yamlPlan} />
          </div>
        )}
        {tab === "log" && <LogTerminal log={state.log} />}
        {tab === "memory" && <SessionMemoryView state={state} />}
      </div>
    </div>
  )
}
