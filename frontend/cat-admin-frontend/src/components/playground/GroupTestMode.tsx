/**
 * GroupTestMode — batch regression testing mode for the Playground.
 *
 * Lets the user select a subset of tools, choose sequential vs parallel
 * execution, and run them all against the real backend via
 * POST /api/playground/tools/invoke with auto-generated dummy payloads.
 * Shows a 4-stat summary dashboard and a detailed results table.
 */

import { getErrorMessage } from "@/utils/errors"
import { useRef, useState, useEffect } from "react"
import {
  Play,
  Check,
  X,
  Activity,
  Spark,
  Download,
} from "@/components/shell/Icons"
import { invokePlaygroundTool } from "@/api/playground"
import { useToolRegistry } from "./useToolRegistry"
import { dummyFromSchema } from "./utils"
import type { GroupTestResult, PGTool } from "./types"

/** Max in-flight invocations in parallel mode — tools hit real external APIs. */
const PARALLEL_LIMIT = 4

/** Invokes one tool with a schema-generated dummy payload; errors become rows. */
async function runOne(tool: PGTool): Promise<GroupTestResult> {
  const args = dummyFromSchema(tool.input) as Record<string, unknown>
  try {
    const r = await invokePlaygroundTool(tool.name, args)
    return {
      tool: tool.name,
      status: r.ok ? "pass" : "fail",
      ms: r.ms,
      tokens: 0,
      err: r.error,
    }
  } catch (e) {
    return {
      tool: tool.name,
      status: "fail",
      ms: 0,
      tokens: 0,
      err: getErrorMessage(e),
    }
  }
}

// ─── Group Test Mode ───────────────────────────────────────────────────────────

/**
 * Batch tool testing: chip-select tools, pick execution order, view aggregated
 * pass/fail/latency stats and a detailed per-tool results table.
 */
export function GroupTestMode() {
  const { tools } = useToolRegistry()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [runMode, setRunMode] = useState<"sequential" | "parallel">("parallel")
  const [results, setResults] = useState<GroupTestResult[] | null>(null)
  const [running, setRunning] = useState(false)
  const [wallMs, setWallMs] = useState(0)
  const seededRef = useRef(false)

  const plugins = Array.from(new Set(tools.map((t) => t.plugin)))

  // Default to all tools selected once the registry loads
  useEffect(() => {
    if (!seededRef.current && tools.length > 0) {
      seededRef.current = true
      setSelected(new Set(tools.map((t) => t.name)))
    }
  }, [tools])

  const toggle = (name: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(name)) n.delete(name)
      else n.add(name)
      return n
    })

  const handleRun = async () => {
    if (running) return
    const targets = tools.filter((t) => selected.has(t.name))
    if (targets.length === 0) return
    setRunning(true)
    setResults([])
    const started = performance.now()

    const push = (r: GroupTestResult) =>
      setResults((prev) => [...(prev ?? []), r])

    try {
      if (runMode === "sequential") {
        for (const t of targets) {
          push(await runOne(t))
        }
      } else {
        // Bounded promise pool — never more than PARALLEL_LIMIT in flight.
        const queue = [...targets]
        const worker = async () => {
          for (;;) {
            const t = queue.shift()
            if (!t) return
            push(await runOne(t))
          }
        }
        await Promise.all(
          Array.from({ length: Math.min(PARALLEL_LIMIT, queue.length) }, worker)
        )
      }
    } finally {
      setWallMs(Math.round(performance.now() - started))
      setRunning(false)
    }
  }

  const passed   = results?.filter((r) => r.status === "pass").length ?? 0
  const failed   = results?.filter((r) => r.status === "fail").length ?? 0
  const skipped  = results?.filter((r) => r.status === "skip").length ?? 0
  const nonSkip  = results?.filter((r) => r.status !== "skip") ?? []
  const avgMs    = nonSkip.length
    ? Math.round(nonSkip.reduce((s, r) => s + r.ms, 0) / nonSkip.length)
    : 0

  return (
    <div className="pg-group">
      {/* Run controls */}
      <div className="pg-group-controls">
        {/* Tool chip selector */}
        <div className="pg-group-pick">
          <span className="ct-eyebrow">selection</span>
          <div className="pg-group-tools">
            {tools.map((t) => {
              const on = selected.has(t.name)
              return (
                <button
                  key={t.name}
                  className={"pg-chip" + (on ? " is-on" : "")}
                  onClick={() => toggle(t.name)}
                >
                  <span className={"pg-chip-box" + (on ? " is-on" : "")}>
                    {on && <Check width="10" height="10" />}
                  </span>
                  <span className="pg-chip-name">{t.name}</span>
                </button>
              )
            })}
          </div>
          <div className="pg-group-quick">
            <button
              className="ct-btn-ghost"
              style={{ padding: "4px 9px", fontSize: 11 }}
              onClick={() => setSelected(new Set(tools.map((t) => t.name)))}
            >
              all
            </button>
            <button
              className="ct-btn-ghost"
              style={{ padding: "4px 9px", fontSize: 11 }}
              onClick={() => setSelected(new Set())}
            >
              none
            </button>
            {plugins.map((p) => (
              <button
                key={p}
                className="ct-btn-ghost"
                style={{ padding: "4px 9px", fontSize: 11 }}
                onClick={() =>
                  setSelected(
                    new Set(tools.filter((t) => t.plugin === p).map((t) => t.name))
                  )
                }
              >
                {p}
              </button>
            ))}
          </div>
          <div
            style={{
              marginTop: 8,
              fontFamily: "var(--font-mono)",
              fontSize: 10.5,
              color: "var(--warn)",
              letterSpacing: "0.04em",
            }}
          >
            ⚠ fires real tool calls with auto-generated payloads — may create real records
          </div>
        </div>

        {/* Run mode + actions */}
        <div className="pg-group-right">
          <div className="pg-mode">
            {(["sequential", "parallel"] as const).map((m) => (
              <button
                key={m}
                className={"pg-mode-opt" + (runMode === m ? " is-on" : "")}
                onClick={() => setRunMode(m)}
                disabled={running}
              >
                {m}
              </button>
            ))}
          </div>
          <button className="ct-btn-ghost">
            <Download width="13" height="13" /> export json
          </button>
          <button
            className="ct-btn-primary"
            style={{ width: "auto", padding: "10px 18px" }}
            disabled={selected.size === 0 || running}
            onClick={handleRun}
          >
            <Play width="12" height="12" />{" "}
            {running ? `Running ${results?.length ?? 0}/${selected.size}…` : `Run ${selected.size} tools`}
          </button>
        </div>
      </div>

      {/* Results — only shown after a run starts */}
      {results && (
        <>
          {/* 4-stat dashboard */}
          <div className="ct-stats is-4" style={{ marginTop: 18 }}>
            <div className="ct-stat">
              <div className="ct-stat-head">
                <div className="ct-stat-ico is-neon">
                  <Check width="16" height="16" />
                </div>
                <div className="ct-stat-delta is-up">
                  {results.length > 0
                    ? Math.round((passed / results.length) * 100)
                    : 0}
                  % pass
                </div>
              </div>
              <div className="ct-stat-label">Passed</div>
              <div className="ct-stat-value">
                {passed}
                <span className="ct-stat-sub">/ {results.length}</span>
              </div>
            </div>

            <div className="ct-stat">
              <div className="ct-stat-head">
                <div className="ct-stat-ico is-pink">
                  <X width="16" height="16" />
                </div>
                <div className="ct-stat-delta">
                  {skipped > 0 ? `${skipped} skipped` : "none skipped"}
                </div>
              </div>
              <div className="ct-stat-label">Failed</div>
              <div className="ct-stat-value">
                {failed}
                <span className="ct-stat-sub">errors</span>
              </div>
            </div>

            <div className="ct-stat">
              <div className="ct-stat-head">
                <div className="ct-stat-ico">
                  <Activity width="16" height="16" />
                </div>
                <div className="ct-stat-delta">mean</div>
              </div>
              <div className="ct-stat-label">Avg latency</div>
              <div className="ct-stat-value">
                {avgMs}
                <span className="ct-stat-sub">ms</span>
              </div>
            </div>

            <div className="ct-stat">
              <div className="ct-stat-head">
                <div className="ct-stat-ico is-cyan">
                  <Spark width="16" height="16" />
                </div>
                <div className="ct-stat-delta">{runMode}</div>
              </div>
              <div className="ct-stat-label">Wall clock</div>
              <div className="ct-stat-value">
                {running ? "…" : (wallMs / 1000).toFixed(1)}
                <span className="ct-stat-sub">s</span>
              </div>
            </div>
          </div>

          {/* Results table */}
          <div className="ct-section" style={{ marginTop: 14 }}>
            <div className="ct-section-head">
              <div>
                <div className="ct-section-title">
                  Run results
                  <span className="ct-pill" style={{ marginLeft: 4 }}>
                    {runMode} · {results.length} calls
                  </span>
                </div>
                <div className="ct-section-sub">
                  Auto-generated payloads from each tool's input schema. Pass = tool returned without error.
                </div>
              </div>
              <span className="ct-eyebrow">
                {running
                  ? `running… ${results.length}/${selected.size}`
                  : `finished ${new Date().toLocaleTimeString()} · ${(wallMs / 1000).toFixed(1)}s wall`}
              </span>
            </div>
            <table className="ct-table">
              <thead>
                <tr>
                  <th />
                  <th>tool</th>
                  <th>plugin</th>
                  <th>latency</th>
                  <th>result</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => {
                  const tool = tools.find((t) => t.name === r.tool)
                  const pillCls =
                    r.status === "pass"
                      ? "is-ok"
                      : r.status === "fail"
                        ? "is-err"
                        : ""
                  const dotCls =
                    r.status === "pass"
                      ? "is-ok"
                      : r.status === "fail"
                        ? "is-err"
                        : "is-off"
                  return (
                    <tr key={r.tool}>
                      <td style={{ width: 36 }}>
                        <span className={"ct-dot " + dotCls} />
                      </td>
                      <td className="tool-name">{r.tool}</td>
                      <td className="mono">{tool?.plugin}</td>
                      <td className="mono">
                        {r.status === "skip" ? "—" : `${r.ms}ms`}
                      </td>
                      <td>
                        <span className={"ct-pill " + pillCls}>{r.status}</span>
                        {r.err && (
                          <span
                            style={{
                              marginLeft: 10,
                              fontFamily: "var(--font-mono)",
                              fontSize: 11.5,
                              color: "var(--fg-muted)",
                            }}
                          >
                            {r.err}
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
