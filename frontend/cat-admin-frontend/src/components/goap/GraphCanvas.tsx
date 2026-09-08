import React, { useMemo, useState, memo } from "react"
import type { NodeId, SimNodeState, EdgeKind, NodeDef } from "./types"
import { NODE_REGISTRY } from "./NodeRegistry"
import type { BackendGraph } from "../../api/playground"
import { layoutGraph, loopArcHeight } from "./graphLayout"
import { GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY } from "./graphTopology.gen"
import { auditByNode, type ModelAuditEntry } from "./modelAudit"

/** Node box width — larger for readable labels without shrinking the SVG. */
const NW = 145
/** Node box height. */
const NH = 56

function nodeStyle(status: SimNodeState["status"]) {
  switch (status) {
    case "active":  return { fill: "oklch(0.23 0.022 48)", stroke: "oklch(0.82 0.165 70)",  strokeW: 1.8, glow: true,  textColor: "oklch(0.82 0.165 70)" }
    case "done":    return { fill: "oklch(0.21 0.030 145)", stroke: "oklch(0.86 0.200 145)", strokeW: 1.5, glow: false, textColor: "oklch(0.86 0.200 145)" }
    case "error":   return { fill: "oklch(0.22 0.040 25)",  stroke: "oklch(0.70 0.190 25)",  strokeW: 1.5, glow: false, textColor: "oklch(0.70 0.190 25)" }
    case "skipped": return { fill: "oklch(0.16 0.012 42)",  stroke: "oklch(0.28 0.022 50)",  strokeW: 1,   glow: false, textColor: "oklch(0.48 0.018 55)" }
    case "waiting": return { fill: "oklch(0.23 0.022 48)",  stroke: "oklch(0.72 0.130 80)",  strokeW: 1.5, glow: false, textColor: "oklch(0.82 0.120 80)" }
    default:        return { fill: "oklch(0.21 0.020 46)",  stroke: "oklch(0.32 0.025 55)",  strokeW: 1,   glow: false, textColor: "oklch(0.62 0.022 60)" }
  }
}

function edgeStyle(kind: EdgeKind, active: boolean) {
  const color: Record<EdgeKind, string> = {
    main:    active ? "oklch(0.86 0.200 145)" : "oklch(0.35 0.022 55)",
    proceed: active ? "oklch(0.82 0.165 70)"  : "oklch(0.35 0.022 55)",
    loop:    active ? "oklch(0.82 0.130 200)" : "oklch(0.30 0.018 50)",
  }
  const dash = kind === "loop" ? "5,4" : null
  return { strokeWidth: 1.4, color: color[kind], dasharray: dash }
}

function NodeBox({ meta, x, y, status, onSelect, selected, isRouter, audit }: {
  meta: NodeDef; x: number; y: number; status: SimNodeState["status"]; onSelect: (id: NodeId) => void; selected: boolean; isRouter: boolean
  audit?: ModelAuditEntry[]
}) {
  const s = nodeStyle(status)
  const [isFocused, setIsFocused] = useState(false)

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      if (e.key === " ") {
        e.preventDefault()
      }
      onSelect(meta.id)
    }
  }

  const cxVal = x + NW / 2
  const cyVal = y + NH / 2

  return (
    <g
      className="graph-node"
      role="button"
      tabIndex={0}
      aria-label={meta.label || meta.short}
      style={{
        cursor: "pointer",
        outline: isFocused ? "2px solid var(--amber)" : "none",
        outlineOffset: "2px",
      }}
      onClick={() => onSelect(meta.id)}
      onKeyDown={handleKeyDown}
      onFocus={() => setIsFocused(true)}
      onBlur={() => setIsFocused(false)}
    >
      {s.glow && (
        <rect x={x - 5} y={y - 5} width={NW + 10} height={NH + 10} rx={13} ry={13}
          fill="none" stroke="oklch(0.82 0.165 70)" strokeWidth={8} opacity={0.18}
          style={{ filter: "blur(6px)" }} />
      )}
      {selected && (
        <rect x={x - 3} y={y - 3} width={NW + 6} height={NH + 6} rx={11} ry={11}
          fill="none" stroke="oklch(0.82 0.165 70)" strokeWidth={1.5} opacity={0.5} strokeDasharray="4,3" />
      )}
      <rect x={x} y={y} width={NW} height={NH} rx={9} ry={9}
        fill={s.fill} stroke={s.stroke} strokeWidth={s.strokeW} />
      {isRouter && (
        <polygon
          points={`${x + NW - 14},${cyVal - 6} ${x + NW - 8},${cyVal} ${x + NW - 14},${cyVal + 6} ${x + NW - 20},${cyVal}`}
          fill={s.stroke} opacity={0.8} />
      )}
      <text x={cxVal} y={y + 20} textAnchor="middle" fontSize={12} fontWeight={600}
        fontFamily="'JetBrains Mono', monospace" letterSpacing="0.04em" fill={s.textColor}>
        {meta.short}
      </text>
      <text x={cxVal} y={y + 38} textAnchor="middle" fontSize={9}
        fontFamily="'JetBrains Mono', monospace" letterSpacing="0.08em" fill={s.textColor} opacity={0.65}>
        {meta.label}
      </text>
      {status === "done" && (
        <circle cx={x + NW - 9} cy={y + 9} r={5} fill="oklch(0.86 0.200 145)" />
      )}
      {status === "error" && (
        <>
          <line x1={x + NW - 13} y1={y + 5}  x2={x + NW - 5}  y2={y + 13}
            stroke="oklch(0.70 0.190 25)" strokeWidth={2} strokeLinecap="round" />
          <line x1={x + NW - 5}  y1={y + 5}  x2={x + NW - 13} y2={y + 13}
            stroke="oklch(0.70 0.190 25)" strokeWidth={2} strokeLinecap="round" />
        </>
      )}
      {status === "active" && (
        <circle cx={x + NW - 9} cy={y + 9} r={4} fill="oklch(0.82 0.165 70)">
          <animate attributeName="opacity" values="1;0.3;1" dur="1.2s" repeatCount="indefinite" />
        </circle>
      )}
      {audit && audit.length > 0 && (() => {
        const escalated = audit.some((a) => a.escalated)
        const chipColor = escalated ? "oklch(0.82 0.165 70)" : "oklch(0.62 0.16 200)"
        // Model names get long — show the last dash/slash-delimited segment.
        const label = audit[0].model?.split(/[-/]/).pop() || audit[0].model || "?"
        return (
          <g>
            <title>{audit.map((a) => `${a.role}: rung ${a.rung_index} -> ${a.model} (${a.status})`).join("\n")}</title>
            <rect x={x + 4} y={y + NH - 15} width={NW - 8} height={12} rx={5} ry={5}
              fill={chipColor} opacity={0.16} stroke={chipColor} strokeWidth={0.75} />
            <text x={x + 9} y={y + NH - 6} fontSize={7.5} fontFamily="'JetBrains Mono', monospace"
              letterSpacing="0.03em" fill={chipColor} opacity={0.95}>
              {escalated ? `⇡ ${label}` : label}
            </text>
          </g>
        )
      })()}
    </g>
  )
}

function EdgeArrow({ edge, active, path, labelX, labelY, showLabel }: {
  edge: { id: string; from: string; to: string; kind: EdgeKind; label?: string }; active: boolean; path: string; labelX: number; labelY: number; showLabel: boolean
}) {
  if (!path) return null
  const s = edgeStyle(edge.kind, active)
  const markId = `arr-${edge.kind}-${active ? "a" : "i"}`

  return (
    <g>
      <path d={path} fill="none" stroke={s.color} strokeWidth={s.strokeWidth}
        strokeDasharray={s.dasharray ?? undefined} strokeLinecap="round"
        markerEnd={`url(#${markId})`} opacity={active ? 1 : 0.55} />
      {showLabel && (
        <text x={labelX} y={labelY} textAnchor="middle" fontSize={8.5}
          fontFamily="'JetBrains Mono', monospace" letterSpacing="0.07em"
          fill={s.color} opacity={active ? 0.9 : 0.5}>
          {edge.label}
        </text>
      )}
    </g>
  )
}

/**
 * Canvas component for rendering GOAP graphs.
 * Uses explicit SVG pixel size + horizontal scroll so nodes stay crisp and large.
 */
export const GraphCanvas = memo(function GraphCanvas({
  nodes,
  selectedNode,
  onSelectNode,
  backendGraph,
  rawState,
}: {
  nodes: Record<NodeId, SimNodeState>
  selectedNode: NodeId | null
  onSelectNode: (id: NodeId) => void
  backendGraph?: BackendGraph
  rawState?: Record<string, unknown>
}) {
  const edgeKinds: EdgeKind[] = ["main", "proceed", "loop"]
  const auditMap = useMemo(() => auditByNode(rawState), [rawState])

  // 1. Resolve dynamic nodes list from backendGraph if available, fallback to custom mapped GRAPH_NODES
  const resolvedNodes = useMemo(() => {
    if (!backendGraph || !backendGraph.available || backendGraph.nodes.length === 0) {
      return GRAPH_NODES.map(n => NODE_REGISTRY.find(r => r.id === n.id)).filter(Boolean) as NodeDef[]
    }
    return backendGraph.nodes.map(bn => {
      const staticDef = NODE_REGISTRY.find(n => n.id === bn.id)
      if (staticDef) return staticDef

      const shortName = bn.id.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")
      return {
        id: bn.id as NodeId,
        label: `${bn.id}_node`,
        short: shortName,
        x: 0,
        y: 0,
        type: bn.id.includes("router") || bn.id.includes("gate") || bn.id === "goap_goal" || bn.id === "triage" ? "router" : "process",
        derive: (s) => {
          const hasError = s.response && (s.response as Record<string, unknown>).status === "error"
          const isActive = s.active_node === bn.id
          return {
            status: isActive ? (hasError ? "error" : "active") : "idle",
            output: s
          }
        },
        render: (out) => (
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg-muted)", whiteSpace: "pre-wrap" }}>
            Node execution state: {JSON.stringify(out, null, 2)}
          </div>
        )
      } as NodeDef
    })
  }, [backendGraph])

  // 2. Compute dynamic layout using layoutGraph
  const layout = useMemo(() => {
    if (backendGraph && backendGraph.available && backendGraph.nodes.length > 0) {
      return layoutGraph(backendGraph.nodes, backendGraph.edges, "turn_init")
    }
    return layoutGraph(GRAPH_NODES, GRAPH_EDGES, GRAPH_ENTRY)
  }, [backendGraph])

  // 3. Derive edge path definitions and label coordinates
  const edgePathData = useMemo(() => {
    let loopIndex = 0
    return layout.edges.map(edge => {
      const sx = layout.positions[edge.from]?.x ?? 0
      const sy = layout.positions[edge.from]?.y ?? 0
      const tx = layout.positions[edge.to]?.x ?? 0
      const ty = layout.positions[edge.to]?.y ?? 0
      const startCX = sx + NW / 2
      const endCX = tx + NW / 2

      let path = ""
      let lx = 0
      let ly = 0
      let showLabel = false

      let label: string | undefined = undefined
      if (edge.kind === "loop") {
        label = edge.from === "step_dispatcher" ? "next step" : "replan"
      } else if (edge.from === "confirm_node" && edge.to === "context_check") {
        label = "approved"
      }

      if (edge.kind === "main") {
        // Source right-center → target left-center; stay outside node boxes.
        const startPathX = sx + NW + 2
        const endPathX = tx - 2
        const startPathY = sy + NH / 2
        const endPathY = ty + NH / 2

        const layerOf = (id: string) => layout.layers?.[id] ?? layout.depths[id] ?? 0
        const rowOf = (id: string) => layout.rows?.[id] ?? 0
        const sourceLayer = layerOf(edge.from)
        const targetLayer = layerOf(edge.to)
        const sourceRow = rowOf(edge.from)
        const targetRow = rowOf(edge.to)
        const deltaLayer = Math.abs(targetLayer - sourceLayer)

        if (Math.abs(startPathY - endPathY) < 0.5) {
          // Same row: clean horizontal line along the main corridor.
          path = `M ${startPathX},${startPathY} L ${endPathX},${endPathY}`
        } else if (deltaLayer <= 1) {
          // Adjacent column row transition (e.g. planner col 4 row 0 -> confirm_node col 5 row 1)
          const midX = (startPathX + endPathX) / 2
          path = `M ${startPathX},${startPathY} C ${midX},${startPathY} ${midX},${endPathY} ${endPathX},${endPathY}`
        } else {
          // Multi-column row transition (e.g. step_resolver col 6 row 1 -> goap_goal col 11 row 2)
          // Route along the inter-row gap (gapY) so it never cuts across intermediate nodes in Row 1!
          const maxRow = Math.max(sourceRow, targetRow)
          const gapY = 48 + maxRow * 95 - 20
          const cp1X = startPathX + 24
          const cp2X = endPathX - 24
          path = `M ${startPathX},${startPathY} C ${cp1X},${startPathY} ${cp1X},${gapY} ${startPathX + 40},${gapY} L ${endPathX - 40},${gapY} C ${cp2X},${gapY} ${cp2X},${endPathY} ${endPathX},${endPathY}`
        }
        showLabel = !!label
        lx = (startPathX + endPathX) / 2
        ly = (startPathY + endPathY) / 2 - 8
      } else if (edge.kind === "proceed") {
        // Same-column edges: attach to top/bottom centers so the stroke never
        // enters a node rectangle (e.g. confirm_node → context_check upward).
        if (ty < sy) {
          // Upward: top-center of source → bottom-center of target
          const startPathY = sy - 2
          const endPathY = ty + NH + 2
          // Side-loop port keeps the polyline off any intermediate boxes.
          const sideX = Math.min(sx, tx) - 22
          path = `M ${startCX},${startPathY} L ${sideX},${startPathY} L ${sideX},${endPathY} L ${endCX},${endPathY}`
          showLabel = !!label
          lx = sideX - 4
          ly = (startPathY + endPathY) / 2
        } else if (ty > sy) {
          // Downward: bottom-center of source → top-center of target
          const startPathY = sy + NH + 2
          const endPathY = ty - 2
          const sideX = Math.min(sx, tx) - 22
          path = `M ${startCX},${startPathY} L ${sideX},${startPathY} L ${sideX},${endPathY} L ${endCX},${endPathY}`
          showLabel = !!label
          lx = sideX - 4
          ly = (startPathY + endPathY) / 2
        } else {
          // Degenerate same-position: small side loop
          const sideX = sx - 28
          path = `M ${startCX},${sy + NH / 2} L ${sideX},${sy + NH / 2} L ${sideX},${ty + NH / 2} L ${endCX},${ty + NH / 2}`
        }
      } else if (edge.kind === "loop") {
        // Backward edges: staggered under-arcs so replan / next-step labels
        // and strokes do not pile on the same path. Span uses visual layers.
        const startPathY = sy + NH + 2
        const layerOf = (id: string) => layout.layers?.[id] ?? layout.depths[id] ?? 0
        const rowOf = (id: string) => layout.rows?.[id] ?? 0
        const span = Math.abs(layerOf(edge.from) - layerOf(edge.to))
        const targetRow = rowOf(edge.to)
        const idx = loopIndex++
        const arc = loopArcHeight(span, idx)

        if (targetRow === 0) {
          // Target in Row 0 (e.g. retry_node -> permission_gate col 6 row 0).
          // Route ascending line up inter-column gap (tx - 22) into left port to avoid row 1 nodes!
          const endPathX = tx - 2
          const endPathY = ty + NH / 2
          const gapX = tx - 22
          const bottomY = Math.max(sy, ty) + NH + 2 + arc
          path = `M ${startCX},${startPathY} C ${startCX},${bottomY} ${gapX},${bottomY} ${gapX},${endPathY + 20} C ${gapX},${endPathY} ${gapX},${endPathY} ${endPathX},${endPathY}`
          showLabel = !!label
          lx = (startCX + gapX) / 2
          ly = bottomY - 10 - (idx % 2) * 10
        } else {
          // Target in lower row: standard bottom-to-bottom curve
          const endPathY = ty + NH + 2
          path = `M ${startCX},${startPathY} C ${startCX},${startPathY + arc} ${endCX},${endPathY + arc} ${endCX},${endPathY}`
          showLabel = !!label
          lx = (startCX + endCX) / 2
          ly = Math.max(startPathY, endPathY) + arc - 10 - (idx % 2) * 10
        }
      }

      return {
        id: edge.id,
        edge: { ...edge, label },
        path,
        lx,
        ly,
        showLabel
      }
    })
  }, [layout.edges, layout.positions, layout.depths, layout.layers])

  // 4. Determine active edges based on node statuses
  const activeEdges = useMemo(() => {
    const active = new Set<string>()
    const isDone = (id: NodeId) => nodes[id]?.status === "done"
    const isActive = (id: NodeId) => nodes[id]?.status === "active"

    layout.edges.forEach((e) => {
      if ((isDone(e.from as NodeId) || isActive(e.from as NodeId)) && (isDone(e.to as NodeId) || isActive(e.to as NodeId))) {
        active.add(e.id)
      }
    })
    return active
  }, [nodes, layout.edges])

  // Expand height if staggered loop arcs extend past the layout default.
  const svgW = layout.width
  const loopBottom = useMemo(() => {
    let maxY = layout.height
    let loopIndex = 0
    const layerOf = (id: string) =>
      layout.layers?.[id] ?? layout.depths[id] ?? 0
    for (const edge of layout.edges) {
      if (edge.kind !== "loop") continue
      const sy = layout.positions[edge.from]?.y ?? 0
      const ty = layout.positions[edge.to]?.y ?? 0
      const span = Math.abs(layerOf(edge.from) - layerOf(edge.to))
      const arc = loopArcHeight(span, loopIndex++)
      const bottom = Math.max(sy, ty) + NH + 2 + arc + 24
      if (bottom > maxY) maxY = bottom
    }
    return maxY
  }, [layout])
  const svgH = Math.max(layout.height, loopBottom, 380)

  // Interactive Pan & Zoom state
  const [zoom, setZoom] = useState(1.0)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isMouseDown, setIsMouseDown] = useState(false)
  const containerRef = React.useRef<HTMLDivElement>(null)
  const dragStartRef = React.useRef<{ x: number; y: number }>({ x: 0, y: 0 })
  const panStartRef = React.useRef<{ x: number; y: number }>({ x: 0, y: 0 })
  const hasDraggedRef = React.useRef(false)

  // Attach non-passive wheel listener for smooth zoom-in/out
  React.useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.1 : 0.9
      setZoom(prev => Math.max(0.35, Math.min(2.5, prev * factor)))
    }

    el.addEventListener("wheel", handleWheel, { passive: false })
    return () => el.removeEventListener("wheel", handleWheel)
  }, [])

  const handleMouseDown = (e: React.MouseEvent) => {
    // Only left click initiates drag pan
    if (e.button !== 0) return
    setIsMouseDown(true)
    hasDraggedRef.current = false
    dragStartRef.current = { x: e.clientX, y: e.clientY }
    panStartRef.current = { ...pan }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isMouseDown) return
    const dx = e.clientX - dragStartRef.current.x
    const dy = e.clientY - dragStartRef.current.y
    if (Math.hypot(dx, dy) > 4) {
      hasDraggedRef.current = true
    }
    setPan({
      x: panStartRef.current.x + dx,
      y: panStartRef.current.y + dy,
    })
  }

  const handleMouseUp = () => {
    setIsMouseDown(false)
  }

  const handleNodeSelect = (id: NodeId) => {
    // Ignore node select if user was dragging the canvas
    if (hasDraggedRef.current) return
    onSelectNode(id)
  }

  const handleResetZoom = () => {
    setZoom(1.0)
    setPan({ x: 0, y: 0 })
  }

  const handleFitZoom = React.useCallback(() => {
    if (!containerRef.current) return
    const cw = containerRef.current.clientWidth
    const ch = containerRef.current.clientHeight
    const fitScale = Math.max(0.35, Math.min(1.2, Math.min((cw - 32) / svgW, (ch - 32) / (svgH || 380))))
    setZoom(fitScale)
    setPan({ x: 16, y: 16 })
  }, [svgW, svgH])

  // Auto-fit graph view when canvas mounts or resizes (rAF-throttle setState storms)
  React.useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === "undefined") return
    let raf = 0
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const cw = el.clientWidth
        const ch = el.clientHeight
        if (cw > 0 && ch > 0) {
          const fitScale = Math.max(0.35, Math.min(1.2, Math.min((cw - 32) / svgW, (ch - 32) / (svgH || 380))))
          setZoom(fitScale)
          setPan({ x: 16, y: 16 })
        }
      })
    })
    observer.observe(el)
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
    }
  }, [svgW, svgH])

  return (
    <div
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      style={{
        width: "100%",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
        borderRadius: 12,
        position: "relative",
        cursor: isMouseDown ? "grabbing" : "grab",
        userSelect: "none",
        backgroundColor: "oklch(0.155 0.016 43)",
      }}
    >
      {/* Floating Canvas Controls HUD */}
      <div
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          zIndex: 30,
          display: "flex",
          alignItems: "center",
          gap: 4,
          background: "oklch(0.20 0.018 45 / 0.92)",
          backdropFilter: "blur(8px)",
          WebkitBackdropFilter: "blur(8px)",
          border: "1px solid oklch(0.35 0.02 55)",
          borderRadius: 8,
          padding: "4px 8px",
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11,
          color: "oklch(0.85 0.02 70)",
          boxShadow: "0 4px 12px rgba(0, 0, 0, 0.4)",
        }}
      >
        <button
          type="button"
          onClick={() => setZoom(z => Math.max(0.35, z / 1.15))}
          title="Zoom Out"
          style={{
            background: "none", border: "none", color: "inherit", cursor: "pointer", padding: "2px 6px", borderRadius: 4,
          }}
        >
          −
        </button>
        <span style={{ minWidth: 40, textAlign: "center", opacity: 0.85 }}>
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          onClick={() => setZoom(z => Math.min(2.5, z * 1.15))}
          title="Zoom In"
          style={{
            background: "none", border: "none", color: "inherit", cursor: "pointer", padding: "2px 6px", borderRadius: 4,
          }}
        >
          +
        </button>
        <div style={{ width: 1, height: 14, background: "oklch(0.32 0.02 50)", margin: "0 2px" }} />
        <button
          type="button"
          onClick={handleResetZoom}
          title="Reset View"
          style={{
            background: "none", border: "none", color: "inherit", cursor: "pointer", padding: "2px 6px", borderRadius: 4,
          }}
        >
          Reset
        </button>
        <button
          type="button"
          onClick={handleFitZoom}
          title="Fit Canvas Width"
          style={{
            background: "none", border: "none", color: "inherit", cursor: "pointer", padding: "2px 6px", borderRadius: 4,
          }}
        >
          Fit
        </button>
      </div>

      <div
        style={{
          width: svgW,
          height: svgH,
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transformOrigin: "0 0",
          transition: isMouseDown ? "none" : "transform 0.05s ease-out",
        }}
      >
        <svg
          viewBox={`0 0 ${svgW} ${svgH}`}
          width={svgW}
          height={svgH}
          style={{ display: "block" }}
        >
          <defs>
            {edgeKinds.map(kind =>
              [true, false].map(active => {
                const s = edgeStyle(kind, active)
                return (
                  <marker key={`${kind}-${active}`} id={`arr-${kind}-${active ? "a" : "i"}`}
                    markerWidth={7} markerHeight={7} refX={5} refY={3.5} orient="auto">
                    <path d="M0,0.5 L5.5,3.5 L0,6.5 Z" fill={s.color} opacity={active ? 1 : 0.55} />
                  </marker>
                )
              })
            )}
            <pattern id="goap-grid" width={34} height={34} patternUnits="userSpaceOnUse">
              <path d="M 34 0 L 0 0 0 34" fill="none" stroke="oklch(0.25 0.015 48)" strokeWidth={0.5} opacity={0.5} />
            </pattern>
          </defs>
          <rect x={0} y={0} width={svgW} height={svgH} fill="oklch(0.155 0.016 43)" rx={12} />
          <rect x={0} y={0} width={svgW} height={svgH} fill="url(#goap-grid)" rx={12} opacity={0.4} />

          {/* Section labels — above the node row so they stay out of boxes */}
          {layout.bands.map(({ x, label }) => (
            <text key={label} x={x + NW / 2} y={22} textAnchor="middle" fontSize={8.5}
              fontFamily="'JetBrains Mono', monospace" letterSpacing="0.14em"
              fill="oklch(0.50 0.018 55)" opacity={0.75}>
              {label}
            </text>
          ))}

          {edgePathData.map(ed => (
            <EdgeArrow
              key={ed.id}
              edge={ed.edge}
              active={activeEdges.has(ed.id)}
              path={ed.path}
              labelX={ed.lx}
              labelY={ed.ly}
              showLabel={ed.showLabel}
            />
          ))}

          {resolvedNodes.map(meta => {
            const x = layout.positions[meta.id]?.x ?? 0
            const y = layout.positions[meta.id]?.y ?? 0
            const nodeInfo = GRAPH_NODES.find(n => n.id === meta.id)
            const isRouter = nodeInfo ? nodeInfo.router : (meta.id.includes("router") || meta.id.includes("gate") || meta.id === "goap_goal" || meta.id === "triage")

            return (
              <NodeBox
                key={meta.id}
                meta={meta}
                x={x}
                y={y}
                status={nodes[meta.id]?.status ?? "idle"}
                onSelect={handleNodeSelect}
                selected={selectedNode === meta.id}
                isRouter={isRouter}
                audit={auditMap[meta.id]}
              />
            )
          })}
        </svg>
      </div>
    </div>
  )
})

