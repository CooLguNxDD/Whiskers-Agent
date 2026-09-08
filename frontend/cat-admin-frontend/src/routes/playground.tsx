/**
 * Playground route — MCP client + tool testing sandbox.
 *
 * Thin shell that composes the three playground modes:
 *   Chat       → general-purpose MCP chat (graph agent stream with inline GOAP
 *                DAG per message, or plain default-LLM chat)
 *   Tool Test  → individual tool fire with schema + payload editor
 *   Group Test → batch regression runner with stats dashboard
 */

import { useState } from "react"
import { createFileRoute } from "@tanstack/react-router"
import AppShell from "@/components/shell/AppShell"
import { Download } from "@/components/shell/Icons"
import { PgStrip } from "@/components/playground/PgStrip"
import { McpMode } from "@/components/playground/McpMode"
import { ToolTestMode } from "@/components/playground/ToolTestMode"
import { GroupTestMode } from "@/components/playground/GroupTestMode"

type Mode = "mcp" | "tool" | "group"

/**
 * Playground route configuration.
 */
export const Route = createFileRoute("/playground")({
  component: function PlaygroundPage() {
    const [mode, setMode] = useState<Mode>("mcp")
    const [serverId, setServerId] = useState("local")

    return (
      <AppShell active="playground">
        {/* Page header */}
        <div className="ct-page-head">
          <div>
            <div className="ct-page-title">
              Playground
              <span className="ct-eyebrow">/ mcp client + tool sandbox</span>
            </div>
            <div className="ct-page-sub">
              Test MCP servers directly from the browser. Send goal-loop prompts to any server,
              fire tools individually, or run batched regression tests.
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="ct-pill is-ok">
              <span className="ct-dot is-ok" /> session live · admin@local
            </span>
            <button className="ct-btn-ghost">
              <Download width="13" height="13" /> save run
            </button>
          </div>
        </div>

        {/* Compact Strip Header (replaces ServerBar and pg-modes) */}
        <PgStrip
          mode={mode}
          onModeChange={setMode}
          serverId={serverId}
          onPick={setServerId}
        />

        {/* Active mode panel */}
        <div className="ct-section" style={{ minHeight: 440 }}>
          {mode === "mcp"   && <McpMode />}
          {mode === "tool"  && <ToolTestMode />}
          {mode === "group" && <GroupTestMode />}
        </div>
        <div style={{ height: 12 }} />
      </AppShell>
    )
  },
})
