/**
 * McpMode — "MCP Client-Host" chat mode for the Playground.
 *
 * Path 1 counterpart to ChatMode: instead of REST/SSE, the browser is a real MCP
 * Host that calls the backend `run_graph` tool over a persistent Streamable HTTP
 * session (see `@/api/mcpClient`). Live GOAP state arrives as progress
 * notifications and renders the same inline GoapInline DAG; confirmation and
 * clarification are handled IN-session via MCP elicitation — no second call.
 * Chat history is shared with ChatMode through the same chat_store endpoints.
 */


import { getErrorMessage } from "@/utils/errors"
import { useRef, useState, useEffect, forwardRef, useImperativeHandle, memo, type KeyboardEvent } from "react"
import type { ElicitResult } from "@modelcontextprotocol/sdk/types.js"
import { ChevronDown, Chevron, Search, Send, CatMark, Plus, Bolt } from "@/components/shell/Icons"
import {
  createChatSession,
  listChatSessions,
  getChatSession,
  appendChatMessage,
  type DBChatSession,
  type GraphResponse,
} from "@/api/playground"
import { runGraphMcp, setElicitationResolver, type ElicitationResolver } from "@/api/mcpClient"
import type { LogEntry } from "../goap/types"
import { GoapInline } from "../goap/GoapInline"
import { useToolRegistry } from "./useToolRegistry"
import type { ChatMessage } from "./types"
import { summarizeGraphReply } from "./summarizeGraphReply"
import { FormattedMarkdown } from "./FormattedMarkdown"
import { useShallow } from "zustand/react/shallow"
import { useUIStore } from "@/store"
import { useLlmPoolQuery } from "@/hooks/useConfig"


const MAX_LOG = 500
const EMPTY_OBJECT = {}
const EMPTY_ARRAY: LogEntry[] = []
let logSeq = 0
const mkLog = (msg: string, color = "dim"): LogEntry => ({
  id: Date.now() + logSeq++,
  msg,
  color,
  ts: new Date().toISOString().slice(11, 19),
})

const noop = () => {}

/** A live elicitation request awaiting the operator's answer. */
interface PendingElicit {
  message: string
  kind: "confirm" | "clarify" | "select"
  key: string
  options: string[]
  multi: boolean
  resolve: (r: ElicitResult) => void
}

/** Collapsible raw JSON envelope viewer shown under assistant messages. */
function JSONEnvelopeViewer({ raw }: { raw: GraphResponse }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginTop: 6 }}>
      <button
        type="button"
        className="ct-btn-ghost"
        style={{ fontSize: 11, padding: "2px 6px", height: "auto" }}
        onClick={() => setOpen(!open)}
      >
        {open ? "Hide Envelope" : "View Envelope"}
        <ChevronDown
          width="10"
          height="10"
          style={{ marginLeft: 4, transform: open ? "rotate(180deg)" : "none", transition: "transform 120ms" }}
        />
      </button>
      {open && (
        <pre
          style={{
            margin: "6px 0 0 0",
            fontSize: 11.5,
            color: "var(--fg-muted)",
            fontFamily: "var(--font-mono)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
            background: "var(--bg-sunken)",
            padding: 8,
            borderRadius: 4,
            border: "1px solid var(--hairline)",
          }}
        >
          {JSON.stringify(raw, null, 2)}
        </pre>
      )}
    </div>
  )
}

/** Toggleable GOAP DAG under an assistant message; onConfirm answers a live elicitation. */
function GoapSection({ msg, onConfirm }: { msg: ChatMessage; onConfirm: (yes: boolean) => void }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const open = userOpen ?? !!msg.streaming
  return (
    <div style={{ marginTop: 6 }}>
      <button
        type="button"
        className="ct-btn-ghost"
        style={{ fontSize: 11, padding: "2px 6px", height: "auto" }}
        onClick={() => setUserOpen(!open)}
      >
        {open ? "Hide graph" : "Show graph"}
        <ChevronDown
          width="10"
          height="10"
          style={{ marginLeft: 4, transform: open ? "rotate(180deg)" : "none", transition: "transform 120ms" }}
        />
      </button>
      {open && (
        <div style={{ marginTop: 8 }}>
          <GoapInline
            goapState={msg.goapState ?? EMPTY_OBJECT}
            log={msg.log ?? EMPTY_ARRAY}
            running={!!msg.streaming}
            onConfirm={onConfirm}
          />
        </div>
      )}
    </div>
  )
}

/** Props for ElicitationPrompt. */
interface ElicitationPromptProps {
  pendingElicit: PendingElicit
  clarifyText: string
  setClarifyText: (t: string) => void
  answerElicit: (accept: boolean, textValue?: string | string[]) => void
}

/**
 * ElicitationPrompt — renders inline buttons or input to resolve an MCP elicitation request.
 */
function ElicitationPrompt({
  pendingElicit,
  clarifyText,
  setClarifyText,
  answerElicit,
}: ElicitationPromptProps) {
  const [selected, setSelected] = useState<string[]>([])

  const toggleSelect = (opt: string) => {
    setSelected((prev) =>
      prev.includes(opt) ? prev.filter((o) => o !== opt) : [...prev, opt]
    )
  }

  return (
    <div
      style={{
        marginTop: 8,
        padding: 10,
        border: "1px solid var(--amber)",
        borderRadius: 6,
        background: "var(--bg-sunken)",
      }}
    >
      <div style={{ fontSize: 12, marginBottom: 8, whiteSpace: "pre-wrap" }}>
        {pendingElicit.message}
      </div>
      {pendingElicit.kind === "select" ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {pendingElicit.options.map((opt) => {
            if (pendingElicit.multi) {
              const isSelected = selected.includes(opt)
              return (
                <button
                  key={opt}
                  type="button"
                  className={isSelected ? "ct-btn-primary" : "ct-btn-ghost"}
                  style={{
                    borderColor: "var(--amber)",
                    color: isSelected ? undefined : "var(--amber)",
                    fontWeight: isSelected ? "bold" : "normal",
                  }}
                  onClick={() => toggleSelect(opt)}
                >
                  {opt}
                </button>
              )
            } else {
              return (
                <button
                  key={opt}
                  type="button"
                  className="ct-btn-ghost"
                  style={{ borderColor: "var(--amber)", color: "var(--amber)" }}
                  onClick={() => answerElicit(true, opt)}
                >
                  {opt}
                </button>
              )
            }
          })}
          {pendingElicit.multi && (
            <button
              type="button"
              className="ct-btn-primary"
              disabled={selected.length === 0}
              onClick={() => answerElicit(true, selected)}
            >
              Confirm
            </button>
          )}
          <button type="button" className="ct-btn-ghost" onClick={() => answerElicit(false)}>
            Cancel
          </button>
        </div>
      ) : pendingElicit.kind === "clarify" ? (
        <div style={{ display: "flex", gap: 6 }}>
          <input
            autoFocus
            value={clarifyText}
            onChange={(e) => setClarifyText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") answerElicit(true, clarifyText)
            }}
            placeholder="type your clarification…"
            style={{ flex: 1 }}
          />
          <button type="button" className="ct-btn-primary" onClick={() => answerElicit(true, clarifyText)}>
            <Send width="12" height="12" /> Send
          </button>
          <button type="button" className="ct-btn-ghost" onClick={() => answerElicit(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" className="ct-btn-primary" onClick={() => answerElicit(true)}>
            <Bolt width="12" height="12" /> Approve &amp; Execute
          </button>
          <button type="button" className="ct-btn-ghost" onClick={() => answerElicit(false)}>
            Decline
          </button>
        </div>
      )}
    </div>
  )
}

/** Handle methods exposed by LiveAssistantBubble. */
export interface LiveAssistantBubbleHandle {
  pushToken: (node: string, tokenText: string) => void
  mergeState: (state: Record<string, unknown>) => void
  pushLog: (msg: string, color?: string) => void
}

/** Props for LiveAssistantBubble. */
interface LiveAssistantBubbleProps {
  initialText?: string
  initialGoapState?: Record<string, unknown>
  initialLog?: LogEntry[]
  pendingElicit: PendingElicit | null
  clarifyText: string
  setClarifyText: (t: string) => void
  answerElicit: (accept: boolean, textValue?: string | string[]) => void
}

/**
 * LiveAssistantBubble — handles high-frequency rendering of streaming tokens and GOAP state.
 */
const LiveAssistantBubble = forwardRef<LiveAssistantBubbleHandle, LiveAssistantBubbleProps>(
  (
    {
      initialText = "",
      initialGoapState = EMPTY_OBJECT,
      initialLog = EMPTY_ARRAY,
      pendingElicit,
      clarifyText,
      setClarifyText,
      answerElicit,
    },
    ref
  ) => {
    const [text, setText] = useState(initialText)
    const [goapState, setGoapState] = useState<Record<string, unknown>>(initialGoapState)
    const [log, setLog] = useState<LogEntry[]>(initialLog)

    useImperativeHandle(ref, () => ({
      pushToken(node, tokenText) {
        if (node === "chat_node") {
          setText((prev) => prev + tokenText)
        }
        setLog((prev) => [...prev, mkLog(`[${node || "sys"}] ${tokenText.replace(/\n/g, " ")}`)].slice(-MAX_LOG))
      },
      mergeState(state) {
        setGoapState((prev) => ({ ...prev, ...state }))
      },
      pushLog(msg, color) {
        setLog((prev) => [...prev, mkLog(msg, color)].slice(-MAX_LOG))
      },
    }))

    const msgForGoap: ChatMessage = {
      role: "assistant",
      text,
      goapState,
      log,
      streaming: true,
    }

    return (
      <div className="pg-msg pg-msg-asst">
        <div className="pg-msg-avatar is-bot">
          <CatMark width="18" height="18" />
        </div>
        <div className="pg-msg-stack">
          <div className="pg-msg-bubble is-asst">
            {text ? (
              <FormattedMarkdown content={text} />
            ) : (
              <span style={{ color: "var(--fg-subtle)" }}>
                <span
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: "var(--amber)",
                    animation: "pulse 1s infinite",
                    marginRight: 6,
                  }}
                />
                running graph over mcp…
              </span>
            )}
          </div>
          <GoapSection msg={msgForGoap} onConfirm={(yes) => answerElicit(yes)} />
          {pendingElicit && (
            <ElicitationPrompt
              key={pendingElicit.key + "_" + pendingElicit.message}
              pendingElicit={pendingElicit}
              clarifyText={clarifyText}
              setClarifyText={setClarifyText}
              answerElicit={answerElicit}
            />
          )}
        </div>
      </div>
    )
  }
)

LiveAssistantBubble.displayName = "LiveAssistantBubble"

/** Props for MessageRow. */
interface MessageRowProps {
  msg: ChatMessage
  onConfirm?: (yes: boolean) => void
}

/**
 * MessageRow — memoized row representing a single chat message (user or assistant).
 */
const MessageRow = memo(({ msg, onConfirm }: MessageRowProps) => {
  if (msg.role === "user") {
    return (
      <div className="pg-msg pg-msg-user">
        <div className="pg-msg-avatar">AD</div>
        <div className="pg-msg-bubble">{msg.text}</div>
      </div>
    )
  }
  return (
    <div className="pg-msg pg-msg-asst">
      <div className="pg-msg-avatar is-bot">
        <CatMark width="18" height="18" />
      </div>
      <div className="pg-msg-stack">
        <div className="pg-msg-bubble is-asst">
          <FormattedMarkdown content={msg.text} />
        </div>
        {msg.goapState && (
          <GoapSection msg={msg} onConfirm={onConfirm ?? noop} />
        )}
        {msg.raw && <JSONEnvelopeViewer raw={msg.raw} />}
      </div>
    </div>
  )
})

MessageRow.displayName = "MessageRow"

/**
 * MCP Client-Host chat mode: persistent MCP session, live GOAP DAG from
 * notifications, in-session elicitation for confirm/clarify.
 */
export function McpMode() {
  const {
    playgroundChatHistoryCollapsed,
    togglePlaygroundChatHistoryCollapsed,
    playgroundRegistryCollapsed,
    togglePlaygroundRegistryCollapsed,
  } = useUIStore(
    useShallow((s) => ({
      playgroundChatHistoryCollapsed: s.playgroundChatHistoryCollapsed,
      togglePlaygroundChatHistoryCollapsed: s.togglePlaygroundChatHistoryCollapsed,
      playgroundRegistryCollapsed: s.playgroundRegistryCollapsed,
      togglePlaygroundRegistryCollapsed: s.togglePlaygroundRegistryCollapsed,
    }))
  )
  const [sessions, setSessions] = useState<DBChatSession[]>([])
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [toolQuery, setToolQuery] = useState("")
  const [pendingElicit, setPendingElicit] = useState<PendingElicit | null>(null)
  const [clarifyText, setClarifyText] = useState("")
  const [mcpUrl, setMcpUrl] = useState(() => {
    try {
      return localStorage.getItem("mcp_url") || "/mcp"
    } catch {
      return "/mcp"
    }
  })
  const { tools, loading: toolsLoading } = useToolRegistry()
  const { data: llmPool } = useLlmPoolQuery()
  const activeCoreEntry = llmPool?.entries.find((e) => e.id === llmPool.active.core)
  const oneShotCliProvider = activeCoreEntry && activeCoreEntry.provider.endsWith("-cli")
    ? activeCoreEntry.provider
    : null

  const liveBubbleRef = useRef<LiveAssistantBubbleHandle | null>(null)

  // Persist the custom MCP URL selection locally with a debounce
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        localStorage.setItem("mcp_url", mcpUrl)
      } catch (err) {
        console.warn("Failed to persist mcp_url to local storage", err)
      }
    }, 500)
    return () => clearTimeout(timer)
  }, [mcpUrl])

  // Per-send accumulator — only one stream runs at a time (loading guard).
  const liveRef = useRef<{ state: Record<string, unknown>; text: string; log: LogEntry[] }>({
    state: {},
    text: "",
    log: [],
  })

  useEffect(() => {
    listChatSessions()
      .then((res) => setSessions(res.sessions || []))
      .catch((err) => console.warn("Failed to list chat sessions:", err))
  }, [])

  // Register the elicitation resolver: stash the request so the UI can answer it.
  useEffect(() => {
    interface PropertySchema {
      type?: string
      enum?: unknown[]
      items?: {
        type?: string
        enum?: unknown[]
      }
    }
    interface EnumSchema { enum?: unknown[] }
    const resolver: ElicitationResolver = (message, requestedSchema) =>
      new Promise<ElicitResult>((resolve) => {
        const props = (requestedSchema?.properties ?? {}) as Record<string, PropertySchema>
        const key = Object.keys(props)[0] ?? "value"
        // Detect enum / Literal schema — backend sends this for clarify-with-options.
        const enumValues: string[] | undefined =
          props[key]?.enum?.map(String) ??
          props[key]?.items?.enum?.map(String) ??
          (requestedSchema as EnumSchema)?.enum?.map(String) ??
          undefined
        const kind: "select" | "clarify" | "confirm" = enumValues
          ? "select"
          : props[key]?.type === "string" ? "clarify" : "confirm"
        const multi = props[key]?.type === "array"
        const logMsg = `> elicitation (${kind}): ${message}`
        liveRef.current.log = [...liveRef.current.log, mkLog(logMsg, "amber")]
        liveBubbleRef.current?.pushLog(logMsg, "amber")
        setPendingElicit({ message, kind, key, options: enumValues ?? [], multi, resolve })
      })
    setElicitationResolver(resolver)
    return () => setElicitationResolver(null)
  }, [])

  const filteredTools = toolQuery
    ? tools.filter(
        (t) =>
          t.name.toLowerCase().includes(toolQuery.toLowerCase()) ||
          t.plugin.toLowerCase().includes(toolQuery.toLowerCase()),
      )
    : tools

  const loadSession = async (sessionId: string) => {
    setCurrentSessionId(sessionId)
    setLoading(true)
    try {
      const data = await getChatSession(sessionId)
      const uiMsgs: ChatMessage[] = (data.messages ?? []).map((m) => ({
        role: m.role as "user" | "assistant",
        text:
          m.role === "assistant"
            ? summarizeGraphReply((m.raw as GraphResponse) ?? {}, m.goap_state ?? undefined) || m.content
            : m.content,
        engine: "agent",
        streaming: false,
        originalText: m.role === "user" ? m.content : undefined,
        goapState: m.goap_state ?? undefined,
        raw: m.raw ?? undefined,
        log: m.goap_state ? [mkLog("Loaded from chat history", "green")] : undefined,
      }))
      setMessages(uiMsgs)
    } catch (e) {
      console.error("Failed to load chat session:", e)
    } finally {
      setLoading(false)
    }
  }

  const handleNewChat = () => {
    setCurrentSessionId(null)
    setMessages([])
  }

  /** Answer a live elicitation request and let the same run_graph call resume. */
  const answerElicit = (accept: boolean, textValue?: string | string[]) => {
    const pe = pendingElicit
    if (!pe) return
    setPendingElicit(null)
    setClarifyText("")
    const live = liveRef.current
    if (!accept) {
      const logMsg = "  ↪ operator declined"
      live.log = [...live.log, mkLog(logMsg, "pink")].slice(-MAX_LOG)
      liveBubbleRef.current?.pushLog(logMsg, "pink")
      pe.resolve({ action: "decline" })
      return
    }
    let value: string | string[] | boolean
    if (pe.kind === "clarify") {
      value = textValue ?? ""
    } else if (pe.kind === "select") {
      value = textValue ?? (pe.multi ? [] : "")
    } else {
      value = true
    }
    const logMsg = `  ↪ operator ${pe.kind === "clarify" || pe.kind === "select" ? "answered" : "approved"}`
    live.log = [...live.log, mkLog(logMsg, "green")].slice(-MAX_LOG)
    liveBubbleRef.current?.pushLog(logMsg, "green")
    pe.resolve({ action: "accept", content: { [pe.key]: value } })
  }

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return
    setLoading(true)

    let activeSessionId = currentSessionId
    try {
      if (!activeSessionId) {
        const title = text.slice(0, 30) + (text.length > 30 ? "..." : "")
        const newSession = await createChatSession(title)
        activeSessionId = newSession.id
        setCurrentSessionId(newSession.id)
        const sessList = await listChatSessions()
        setSessions(sessList.sessions || [])
      }
      if (activeSessionId) await appendChatMessage(activeSessionId, "user", text)
    } catch (e) {
      console.warn("Failed to persist user message in chat history:", e)
    }

    liveRef.current = { state: {}, text: "", log: [] }
    setMessages((prev) => [
      ...prev,
      { role: "user" as const, text },
      { role: "assistant" as const, text: "", engine: "agent" as const, streaming: true, originalText: text, goapState: {}, log: [] },
    ].slice(-MAX_LOG))
    setInput("")

    try {
      // Call run_graph over the selected MCP connection URL
      const raw = await runGraphMcp(
        text,
        {
          onState: (state) => {
            const live = liveRef.current
            live.state = { ...live.state, ...state }
            liveBubbleRef.current?.mergeState(state)
          },
          onToken: (node, tokenText) => {
            const live = liveRef.current
            const isUserFacing = node === "chat_node"
            if (isUserFacing) {
              live.text += tokenText
            }
            live.log = [...live.log, mkLog(`[${node || "sys"}] ${tokenText.replace(/\n/g, " ")}`)].slice(-MAX_LOG)
            liveBubbleRef.current?.pushToken(node || "sys", tokenText)
          },
          onKeepalive: (awaiting) => {
            const live = liveRef.current
            live.log = [...live.log, mkLog(`… keepalive (${awaiting})`, "dim")].slice(-MAX_LOG)
            liveBubbleRef.current?.pushLog(`… keepalive (${awaiting})`, "dim")
          },
        },
        mcpUrl,
        activeSessionId,
      )

      const live = liveRef.current
      live.log = [...live.log, mkLog("> ✓ run_graph completed", "green")].slice(-MAX_LOG)
      const finalSummary = summarizeGraphReply(raw, live.state)
      
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last && last.role === "assistant") {
          next[next.length - 1] = {
            ...last,
            text: finalSummary,
            raw,
            log: live.log,
            goapState: live.state,
            streaming: false,
          }
        }
        return next
      })
      
      setLoading(false)

      if (activeSessionId) {
        appendChatMessage(activeSessionId, "assistant", finalSummary, "agent", live.state, raw)
          .then(() => listChatSessions().then((res) => setSessions(res.sessions || [])).catch(() => {}))
          .catch((err) => console.warn("Failed to persist assistant message:", err))
      }
    } catch (err) {
      const live = liveRef.current
      const message = getErrorMessage(err)
      live.log = [...live.log, mkLog(`> ERROR: ${message}`, "pink")].slice(-MAX_LOG)
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last && last.role === "assistant") {
          next[next.length - 1] = {
            ...last,
            text: `Error: ${message}`,
            log: live.log,
            goapState: live.state,
            streaming: false,
          }
        }
        return next
      })
      setLoading(false)
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  return (
    <div
      className={
        "pg-chat" +
        (playgroundChatHistoryCollapsed ? " history-collapsed" : "") +
        (playgroundRegistryCollapsed ? " registry-collapsed" : "")
      }
    >
      {/* Left rail: chat history */}
      <aside className={"pg-history" + (playgroundChatHistoryCollapsed ? " is-collapsed" : "")}>
        <div className="pg-history-head">
          <span className="ct-eyebrow">mcp history</span>
          <span className="ct-pill">{sessions.length} chats</span>
          <button
            className="pg-rail-toggle"
            onClick={togglePlaygroundChatHistoryCollapsed}
            title={playgroundChatHistoryCollapsed ? "Show chat history" : "Hide chat history"}
          >
            <Chevron width="13" height="13" style={{ transform: playgroundChatHistoryCollapsed ? "none" : "rotate(180deg)" }} />
          </button>
        </div>
        <button type="button" className="ct-btn-primary pg-history-btn-new" onClick={handleNewChat} disabled={loading}>
          <Plus width="13" height="13" /> New Chat
        </button>
        <div className="pg-history-list">
          {sessions.map((s) => (
            <button
              key={s.id}
              className={`pg-history-item${currentSessionId === s.id ? " is-active" : ""}`}
              onClick={() => loadSession(s.id)}
              disabled={loading}
            >
              <span className="pg-history-title">{s.title}</span>
              <span className="pg-history-date">
                {s.updated_at
                  ? new Date(s.updated_at).toLocaleDateString() +
                    " " +
                    new Date(s.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                  : ""}
              </span>
            </button>
          ))}
          {sessions.length === 0 && (
            <div style={{ padding: 12, fontSize: 11.5, color: "var(--fg-muted)", textAlign: "center" }}>
              No chat sessions found.
            </div>
          )}
        </div>
      </aside>

      {/* Center: thread + composer */}
      <section className="pg-thread-wrap">
        <div className="pg-thread">
          {messages.length === 0 && (
            <div
              style={{
                padding: "60px 0",
                textAlign: "center",
                color: "var(--fg-subtle)",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
              }}
            >
              mcp client-host · persistent session · in-session elicitation
            </div>
          )}
          {messages.map((msg, i) => {
            if (msg.streaming) {
              return (
                <LiveAssistantBubble
                  key={i}
                  ref={liveBubbleRef}
                  pendingElicit={pendingElicit}
                  clarifyText={clarifyText}
                  setClarifyText={setClarifyText}
                  answerElicit={answerElicit}
                />
              )
            }
            return (
              <MessageRow
                key={i}
                msg={msg}
              />
            )
          })}
        </div>

        <div className="pg-composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="ask anything — driven over a persistent MCP session  (⇧↵ new line)"
            rows={3}
            disabled={loading}
          />
          <div className="pg-composer-bar">
            <span className="ct-eyebrow">transport</span>
            <span className="ct-pill" style={{ color: "var(--amber)", borderColor: "var(--amber)" }}>
              mcp · streamable-http
            </span>
            {oneShotCliProvider && (
              <span
                className="ct-pill"
                title="Active core LLM is a headless CLI provider — the whole turn runs as a single CLI agent spawn instead of the node-by-node GOAP graph."
                style={{ marginLeft: 8, color: "var(--neon)", borderColor: "var(--neon)" }}
              >
                one-shot cli · {oneShotCliProvider === "agy-cli" ? "agy" : "claude"}
              </span>
            )}
            <span className="ct-eyebrow" style={{ marginLeft: 12 }}>url</span>
            <input
              type="text"
              value={mcpUrl}
              onChange={(e) => setMcpUrl(e.target.value)}
              placeholder="/mcp"
              style={{
                background: "var(--bg-sunken)",
                border: "1px solid var(--border)",
                borderRadius: "4px",
                padding: "3px 8px",
                fontSize: "11.5px",
                fontFamily: "var(--font-mono)",
                color: "var(--fg)",
                width: "180px",
                outline: "none",
                transition: "border-color 120ms, box-shadow 120ms",
              }}
              onFocus={(e) => {
                e.target.style.borderColor = "var(--amber)"
                e.target.style.boxShadow = "0 0 0 2px color-mix(in oklch, var(--amber) 12%, transparent)"
              }}
              onBlur={(e) => {
                e.target.style.borderColor = "var(--border)"
                e.target.style.boxShadow = "none"
              }}
              disabled={loading}
            />
            <span className="ct-eyebrow" style={{ marginLeft: 12 }}>tools</span>
            <span className="ct-pill">auto · all {tools.length}</span>
            <span style={{ flex: 1 }} />
            <button className="ct-btn-primary" onClick={() => sendMessage(input)} disabled={loading || !input.trim()}>
              <Send width="13" height="13" /> {loading ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      </section>

      {/* Right rail: tool registry */}
      <aside className={"pg-rail" + (playgroundRegistryCollapsed ? " is-collapsed" : "")}>
        <div className="pg-rail-head">
          <span className="ct-eyebrow">registry</span>
          <span className="ct-pill">{toolsLoading ? "…" : `${tools.length} tools`}</span>
          <button
            className="pg-rail-toggle"
            onClick={togglePlaygroundRegistryCollapsed}
            title={playgroundRegistryCollapsed ? "Show registry" : "Hide registry"}
          >
            <Chevron width="13" height="13" style={{ transform: playgroundRegistryCollapsed ? "rotate(180deg)" : "none" }} />
          </button>
        </div>
        <div className="pg-rail-search">
          <Search width="13" height="13" />
          <input placeholder="filter tools…" value={toolQuery} onChange={(e) => setToolQuery(e.target.value)} />
        </div>
        <div className="pg-rail-list">
          {filteredTools.map((t) => (
            <button key={t.name} className="pg-rail-tool">
              <span className="pg-rail-name">{t.name}</span>
              <span className="pg-rail-plugin">{t.plugin}</span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  )
}
