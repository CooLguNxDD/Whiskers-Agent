import type { EdgeKind } from "./types"

export interface GraphLayout {
  positions: Record<string, { x: number; y: number }>
  /** Pure BFS hop-count from entry (contract / tests). Not used for X placement. */
  depths: Record<string, number>
  /** Visual column index used for X placement and edge-kind classification. */
  layers: Record<string, number>
  /** Visual row band: 0 = main highway, 1 = secondary, 2 = sink. */
  rows: Record<string, number>
  edges: { id: string; from: string; to: string; kind: EdgeKind; label?: string }[]
  width: number
  height: number
  bands: { label: string; x: number }[]
}

const MARGIN_X = 60
const MARGIN_Y = 48
/** Column pitch — generous horizontal breathing room for larger node boxes. */
export const COL_W = 210
/** Row pitch — keeps stacked same-column nodes clear of edge routes. */
export const ROW_H = 95
/** Extra canvas space below the lowest node row for staggered loop arcs. */
const LOOP_PAD = 220

/**
 * Main linear execution highway — each present node gets its own visual column
 * in this order so the corridor is unbroken on row 0.
 */
export const MAIN_STREAM: readonly string[] = [
  "turn_init",
  "triage",
  "decompose",
  "embedder",
  "planner",
  "context_check",
  "permission_gate",
  "builder",
  "executor",
  "validator",
  "step_dispatcher",
  "summary_node",
]

/** Row 1 — parallel / recovery side-paths that still sit near the highway. */
const SECONDARY_NODES = new Set([
  "step_resolver",
  "confirm_node",
  "wait_node",
  "retry_node",
])

/** Row 2 — terminal / chat / clarify sinks; never sit between builder↔executor. */
const SINK_NODES = new Set([
  "goap_goal",
  "clarify_node",
  "chat_node",
])

/**
 * Non-main nodes share a visual column with a related main-stream anchor.
 * goap_goal anchors to summary_node so it is an end sink, not BFS column 7.
 */
const COLUMN_ANCHOR: Record<string, string> = {
  chat_node: "decompose",
  confirm_node: "context_check",
  clarify_node: "context_check",
  step_resolver: "permission_gate",
  wait_node: "executor",
  retry_node: "step_dispatcher",
  goap_goal: "summary_node",
}

/**
 * Staggered vertical arc height for a backward (loop) edge.
 * span = |sourceLayer − targetLayer|; index = ordinal among loop edges.
 */
export function loopArcHeight(span: number, index: number): number {
  return 60 + Math.max(0, span) * 20 + Math.max(0, index) * 18
}

/** Fixed row band for a known node id (main / secondary / sink). */
export function rowBandFor(id: string): number {
  if (SINK_NODES.has(id)) return 2
  if (SECONDARY_NODES.has(id)) return 1
  if (MAIN_STREAM.includes(id)) return 0
  return 1
}

/**
 * Assign visual layers (columns) and rows.
 * Prefer the main-stream highway when enough of it is present; otherwise BFS.
 */
function assignLayersAndRows(
  nodeIds: string[],
  depths: Record<string, number>,
  adj: Record<string, string[]>,
  pred: Record<string, string[]>
): { layers: Record<string, number>; rows: Record<string, number> } {
  const idSet = new Set(nodeIds)
  const layers: Record<string, number> = {}
  const rows: Record<string, number> = {}

  const mainPresent = MAIN_STREAM.filter(id => idSet.has(id))
  // Highway mode needs a real multi-step spine; tiny graphs fall back to BFS.
  const highway = mainPresent.length >= 4

  if (highway) {
    mainPresent.forEach((id, i) => {
      layers[id] = i
      rows[id] = 0
    })

    // Anchored side nodes first (stable, name-driven).
    for (const id of nodeIds) {
      if (layers[id] !== undefined) continue
      const anchor = COLUMN_ANCHOR[id]
      if (anchor !== undefined && layers[anchor] !== undefined) {
        layers[id] = layers[anchor]
        rows[id] = rowBandFor(id)
      }
    }

    // Remaining unknown nodes: place just after max predecessor layer.
    const unresolved = nodeIds.filter(id => layers[id] === undefined)
    // Iterate a few times so chains of unknowns settle.
    for (let pass = 0; pass < nodeIds.length && unresolved.some(id => layers[id] === undefined); pass++) {
      for (const id of unresolved) {
        if (layers[id] !== undefined) continue
        const preds = (pred[id] || []).filter(p => layers[p] !== undefined)
        if (preds.length > 0) {
          layers[id] = Math.max(...preds.map(p => layers[p])) + 1
          rows[id] = rowBandFor(id)
        }
      }
    }
    // Last resort: BFS depth mapped past the main spine.
    const spineMax = Math.max(0, ...Object.values(layers))
    for (const id of nodeIds) {
      if (layers[id] === undefined) {
        layers[id] = spineMax + 1 + (depths[id] ?? 0)
      }
      if (rows[id] === undefined) {
        rows[id] = rowBandFor(id)
      }
    }

    // Force goap_goal onto the summary (end) column + sink row — never BFS col 7.
    if (idSet.has("goap_goal")) {
      rows["goap_goal"] = 2
      if (layers["summary_node"] !== undefined) {
        layers["goap_goal"] = layers["summary_node"]
      } else {
        layers["goap_goal"] = Math.max(...Object.values(layers))
      }
    }
  } else {
    // Pure BFS column placement for arbitrary / small graphs.
    for (const id of nodeIds) {
      layers[id] = depths[id] ?? 0
      rows[id] = rowBandFor(id)
    }
  }

  // Silence unused adj in highway path (kept for future longest-path fallback).
  void adj

  return { layers, rows }
}

/**
 * Calculates layout for a directed graph.
 * - `depths` = pure BFS (stable contract)
 * - `layers` / `rows` = visual highway placement (main / secondary / sink)
 * - Edge kinds compare **visual layers** so rendering matches the canvas.
 */
export function layoutGraph(
  nodes: { id: string }[],
  edges: { source: string; target: string; conditional?: boolean }[],
  entry: string = "turn_init"
): GraphLayout {
  if (nodes.length === 0) {
    return {
      positions: {},
      depths: {},
      layers: {},
      rows: {},
      edges: [],
      width: 0,
      height: 0,
      bands: [],
    }
  }

  let resolvedEntryId = nodes.some(n => n.id === entry) ? entry : null
  if (!resolvedEntryId) {
    const targets = new Set(edges.map(e => e.target))
    const noIncoming = nodes.find(n => !targets.has(n.id))
    resolvedEntryId = noIncoming ? noIncoming.id : nodes[0].id
  }

  // Adjacency + predecessors.
  const adj: Record<string, string[]> = {}
  const pred: Record<string, string[]> = {}
  for (const node of nodes) {
    adj[node.id] = []
    pred[node.id] = []
  }
  for (const edge of edges) {
    if (adj[edge.source]) adj[edge.source].push(edge.target)
    if (pred[edge.target]) pred[edge.target].push(edge.source)
  }

  // BFS depths (contract).
  const depths: Record<string, number> = {}
  const visited = new Set<string>()
  const queue: string[] = [resolvedEntryId]
  visited.add(resolvedEntryId)
  depths[resolvedEntryId] = 0

  let head = 0
  while (head < queue.length) {
    const u = queue[head++]
    for (const v of adj[u] || []) {
      if (!visited.has(v)) {
        visited.add(v)
        depths[v] = depths[u] + 1
        queue.push(v)
      }
    }
  }
  for (const node of nodes) {
    if (!visited.has(node.id)) depths[node.id] = 0
  }

  const nodeIds = nodes.map(n => n.id)
  const { layers, rows } = assignLayersAndRows(nodeIds, depths, adj, pred)

  // Edge kinds from visual layer order (matches what the canvas draws).
  const outputEdges = edges.map((e, idx) => {
    const sourceLayer = layers[e.source] ?? 0
    const targetLayer = layers[e.target] ?? 0
    let kind: EdgeKind = "main"
    if (targetLayer < sourceLayer) {
      kind = "loop"
    } else if (targetLayer === sourceLayer) {
      kind = "proceed"
    }
    return {
      id: `e-${e.source}-${e.target}-${idx}`,
      from: e.source,
      to: e.target,
      kind,
    }
  })

  // Positions from fixed layer × row bands (not packed-per-column ranks).
  const positions: Record<string, { x: number; y: number }> = {}
  for (const id of nodeIds) {
    const col = layers[id] ?? 0
    const row = rows[id] ?? 0
    positions[id] = {
      x: MARGIN_X + col * COL_W,
      y: MARGIN_Y + row * ROW_H,
    }
  }

  const maxLayer = Math.max(0, ...Object.values(layers))
  const maxRow = Math.max(0, ...Object.values(rows))
  const width = MARGIN_X + (maxLayer + 1) * COL_W + 40
  const height = Math.max(380, MARGIN_Y + (maxRow + 1) * ROW_H + LOOP_PAD)

  const anchors = [
    { label: "INPUT", node: "turn_init" },
    { label: "PLAN", node: "planner" },
    { label: "EXECUTE", node: "executor" },
    { label: "SUMMARY·GOAL", node: "summary_node" },
  ]
  const bands: { label: string; x: number }[] = []
  for (const anchor of anchors) {
    if (positions[anchor.node] !== undefined) {
      bands.push({
        label: anchor.label,
        x: positions[anchor.node].x,
      })
    }
  }

  return {
    positions,
    depths,
    layers,
    rows,
    edges: outputEdges,
    width,
    height,
    bands,
  }
}
