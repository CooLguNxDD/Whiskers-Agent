/**
 * McpMode Component Unit Tests
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { McpMode } from "../McpMode"
import { runGraphMcp, type ElicitationResolver } from "@/api/mcpClient"
import { getChatSession } from "@/api/playground"
import { useLlmPoolQuery } from "@/hooks/useConfig"

let goapInlineRenders = 0

// Mock GoapInline to track renders of static graph components
vi.mock("../../goap/GoapInline", () => ({
  GoapInline: () => {
    goapInlineRenders++
    return <div data-testid="goap-inline" />
  },
}))

// Mock UI Store
vi.mock("@/store", () => ({
  useUIStore: () => ({
    playgroundChatHistoryCollapsed: false,
    playgroundRegistryCollapsed: false,
  }),
}))

// Mock tool registry hook
vi.mock("../useToolRegistry", () => ({
  useToolRegistry: () => ({
    tools: [{ name: "search_records", plugin: "core" }],
    loading: false,
  }),
}))

// Mock mcpClient
let registeredResolver: ElicitationResolver | null = null
vi.mock("@/api/mcpClient", () => ({
  runGraphMcp: vi.fn(),
  setElicitationResolver: vi.fn((resolver: ElicitationResolver | null) => {
    if (resolver) {
      registeredResolver = resolver
    }
  }),
}))

// Mock playground api
vi.mock("@/api/playground", () => ({
  listChatSessions: vi.fn().mockResolvedValue({ sessions: [{ id: "session-1", title: "Existing Session", updated_at: "2026-06-20T00:00:00Z" }] }),
  createChatSession: vi.fn().mockResolvedValue({ id: "session-1", title: "Existing Session" }),
  getChatSession: vi.fn(),
  appendChatMessage: vi.fn().mockResolvedValue({}),
}))

// Mock the LLM pool config hook (drives the one-shot CLI badge)
vi.mock("@/hooks/useConfig", () => ({
  useLlmPoolQuery: vi.fn(),
}))

describe("McpMode streaming performance and elicitation", () => {
  beforeEach(() => {
    goapInlineRenders = 0
    registeredResolver = null
    vi.clearAllMocks()
    // Default: no pool data loaded yet — badge must not render, must not crash.
    vi.mocked(useLlmPoolQuery).mockReturnValue({ data: undefined, isPending: true } as ReturnType<typeof useLlmPoolQuery>)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("streams tokens, updates live bubble, and does NOT re-render committed history rows", async () => {
    // 1. Setup mock session with 1 assistant message that has GOAP state (will render GoapInline)
    vi.mocked(getChatSession).mockResolvedValueOnce({
      messages: [
        { role: "user", content: "initial prompt" },
        { role: "assistant", content: "initial reply", goap_state: { active_node: "planner" } },
      ],
    })

    // Mock runGraphMcp with minor delays to allow React to mount the LiveAssistantBubble ref first
    vi.mocked(runGraphMcp).mockImplementationOnce(async (text, callbacks) => {
      await new Promise((resolve) => setTimeout(resolve, 20))
      // Stream state and token
      callbacks.onState({ active_node: "planner", instruction_set: [{ op: "x" }] })
      callbacks.onToken("chat_node", "Hello")
      callbacks.onToken("planner", " thinking...")
      await new Promise((resolve) => setTimeout(resolve, 20))
      return { status: "ok", steps_executed: 1 }
    })

    render(<McpMode />)

    // Wait for history to load
    const historyItem = await screen.findByText("Existing Session")
    fireEvent.click(historyItem)

    // Expand the graph to trigger GoapInline rendering
    const showGraphBtn = await screen.findByRole("button", { name: /show graph/i })
    fireEvent.click(showGraphBtn)

    // Expect GoapInline to have rendered exactly once for the loaded history message
    await waitFor(() => {
      expect(goapInlineRenders).toBe(1)
    })

    // Type a message and send it
    const textarea = screen.getByPlaceholderText(/ask anything/i)
    fireEvent.change(textarea, { target: { value: "test send" } })

    const sendBtn = screen.getByRole("button", { name: /send/i })
    fireEvent.click(sendBtn)

    // Wait for the streaming token "Hello" to appear in the live bubble
    await screen.findByText("Hello")

    // The stream has executed and completed.
    // Assert that the initial committed message was not re-rendered during streaming.
    // Renders: 1 history graph + 1 live shell on send + 1 live state update.
    await waitFor(() => {
      expect(screen.getByText("Done — 1 step(s) executed")).toBeInTheDocument()
    })

    expect(goapInlineRenders).toBe(3)
  })

  it("renders elicitation select prompt and resolves on click", async () => {
    vi.mocked(getChatSession).mockResolvedValueOnce({ messages: [] })

    let resolvePromise: ((value: unknown) => void) | null = null
    const elicitPromise = new Promise((resolve) => {
      resolvePromise = resolve
    })

    vi.mocked(runGraphMcp).mockImplementationOnce(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
      // Trigger elicitation resolver during execution and await it
      if (registeredResolver) {
        await registeredResolver("Choose A or B?", {
          properties: {
            choice: {
              type: "string",
              enum: ["Option A", "Option B"],
            },
          },
        }).then((res) => resolvePromise?.(res))
      }
      return { status: "ok", steps_executed: 1 }
    })

    render(<McpMode />)

    // Send a message to start runGraphMcp
    const textarea = screen.getByPlaceholderText(/ask anything/i)
    fireEvent.change(textarea, { target: { value: "run prompt" } })
    const sendBtn = screen.getByRole("button", { name: /send/i })
    fireEvent.click(sendBtn)

    // Check that elicitation options render
    const optionA = await screen.findByRole("button", { name: "Option A" })
    const optionB = screen.getByRole("button", { name: "Option B" })
    expect(optionA).toBeInTheDocument()
    expect(optionB).toBeInTheDocument()

    // Click Option A to accept
    fireEvent.click(optionA)

    const result = await elicitPromise
    expect(result).toEqual({
      action: "accept",
      content: { choice: "Option A" },
    })

    // Wait for final state updates to settle
    await screen.findByText("Done — 1 step(s) executed")
  })

  it("renders multi-select elicitation prompt, toggles choices, and resolves on confirm click", async () => {
    vi.mocked(getChatSession).mockResolvedValueOnce({ messages: [] })

    let resolvePromise: ((value: unknown) => void) | null = null
    const elicitPromise = new Promise((resolve) => {
      resolvePromise = resolve
    })

    vi.mocked(runGraphMcp).mockImplementationOnce(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
      // Trigger elicitation resolver during execution and await it
      if (registeredResolver) {
        await registeredResolver("Choose options:", {
          properties: {
            value: {
              type: "array",
              items: {
                type: "string",
                enum: ["Op One", "Op Two", "Op Three"],
              },
            },
          },
        }).then((res) => resolvePromise?.(res))
      }
      return { status: "ok", steps_executed: 1 }
    })

    render(<McpMode />)

    // Send a message to start runGraphMcp
    const textarea = screen.getByPlaceholderText(/ask anything/i)
    fireEvent.change(textarea, { target: { value: "run prompt" } })
    const sendBtn = screen.getByRole("button", { name: /send/i })
    fireEvent.click(sendBtn)

    // Check that elicitation options render
    const optionOne = await screen.findByRole("button", { name: "Op One" })
    const optionTwo = screen.getByRole("button", { name: "Op Two" })
    const optionThree = screen.getByRole("button", { name: "Op Three" })
    const confirmBtn = screen.getByRole("button", { name: "Confirm" })

    expect(optionOne).toBeInTheDocument()
    expect(optionTwo).toBeInTheDocument()
    expect(optionThree).toBeInTheDocument()
    expect(confirmBtn).toBeInTheDocument()

    // Confirm button should be disabled initially (no options selected)
    expect(confirmBtn).toBeDisabled()

    // Click Op One to select it, then Op Two
    fireEvent.click(optionOne)
    expect(confirmBtn).not.toBeDisabled()

    fireEvent.click(optionTwo)

    // Click Confirm to resolve
    fireEvent.click(confirmBtn)

    const result = await elicitPromise
    expect(result).toEqual({
      action: "accept",
      content: { value: ["Op One", "Op Two"] },
    })

    // Wait for final state updates to settle
    await screen.findByText("Done — 1 step(s) executed")
  })

  it("does not render the one-shot CLI badge when the active core provider is a cloud provider", async () => {
    vi.mocked(getChatSession).mockResolvedValueOnce({ messages: [] })
    vi.mocked(useLlmPoolQuery).mockReturnValue({
      data: {
        entries: [{ id: "core-1", name: "GPT Core", kind: "core", provider: "openai", model: "gpt-4o", is_active: true, has_api_key: true }],
        active: { core: "core-1" },
      },
      isPending: false,
    } as ReturnType<typeof useLlmPoolQuery>)

    render(<McpMode />)

    await screen.findByPlaceholderText(/ask anything/i)
    expect(screen.queryByText(/one-shot cli/i)).not.toBeInTheDocument()
  })

  it("renders the one-shot CLI badge when the active core provider is claude-cli", async () => {
    vi.mocked(getChatSession).mockResolvedValueOnce({ messages: [] })
    vi.mocked(useLlmPoolQuery).mockReturnValue({
      data: {
        entries: [{ id: "core-1", name: "Claude Code CLI", kind: "core", provider: "claude-cli", model: "", is_active: true, has_api_key: false }],
        active: { core: "core-1" },
      },
      isPending: false,
    } as ReturnType<typeof useLlmPoolQuery>)

    render(<McpMode />)

    expect(await screen.findByText(/one-shot cli · claude/i)).toBeInTheDocument()
  })

  it("renders the one-shot CLI badge for agy-cli", async () => {
    vi.mocked(getChatSession).mockResolvedValueOnce({ messages: [] })
    vi.mocked(useLlmPoolQuery).mockReturnValue({
      data: {
        entries: [{ id: "core-1", name: "Agy CLI", kind: "core", provider: "agy-cli", model: "", is_active: true, has_api_key: false }],
        active: { core: "core-1" },
      },
      isPending: false,
    } as ReturnType<typeof useLlmPoolQuery>)

    render(<McpMode />)

    expect(await screen.findByText(/one-shot cli · agy/i)).toBeInTheDocument()
  })
})
