import { describe, it, expect } from "vitest"
import { summarizeGraphReply, looksLikeEnvelopeBlob } from "../summarizeGraphReply"

describe("looksLikeEnvelopeBlob", () => {
  it("detects stringified graph envelopes", () => {
    const blob = JSON.stringify({
      status: "ok",
      steps_executed: 2,
      data: [{ status: "ok", data: { _meta: { tool: "list_sessions", item_count: 21 } } }],
    })
    expect(looksLikeEnvelopeBlob(blob)).toBe(true)
  })

  it("allows normal prose", () => {
    expect(looksLikeEnvelopeBlob("Found the first Jules session and opened it.")).toBe(false)
  })
})

describe("summarizeGraphReply", () => {
  it("prefers summary over steps_executed fallback", () => {
    const text = summarizeGraphReply({
      status: "ok",
      steps_executed: 2,
      data: [{ id: 1 }],
      summary: "Found two records and sent greetings.",
    })
    expect(text).toBe("Found two records and sent greetings.")
  })

  it("uses goapState.summary when envelope omits message", () => {
    const text = summarizeGraphReply(
      { status: "ok", steps_executed: 1, data: { x: 1 } },
      { summary: "Completed the lookup successfully." }
    )
    expect(text).toBe("Completed the lookup successfully.")
  })

  it("uses chat reply for chat mode", () => {
    const text = summarizeGraphReply({
      status: "chat",
      message: "Hello! How can I help?",
    })
    expect(text).toBe("Hello! How can I help?")
  })

  it("does not stringify data payload", () => {
    const text = summarizeGraphReply({
      status: "ok",
      steps_executed: 1,
      data: { nested: { deep: true } },
    })
    expect(text).toBe("Done — 1 step(s) executed")
    expect(text).not.toContain("nested")
  })

  it("rejects message that is a stringified envelope", () => {
    const envelope = {
      status: "ok",
      steps_executed: 2,
      data: [
        {
          status: "ok",
          data: {
            _meta: {
              tool: "list_sessions",
              raw_size_bytes: 275742,
              shaped: true,
              item_count: 21,
            },
          },
        },
      ],
    }
    const text = summarizeGraphReply({
      ...envelope,
      message: JSON.stringify(envelope),
      summary: JSON.stringify(envelope),
    })
    expect(text).not.toContain('"steps_executed"')
    expect(text).toMatch(/list_sessions|Done — 2 step/)
  })

  it("uses shaped _meta when NL summary is missing", () => {
    const text = summarizeGraphReply({
      status: "ok",
      steps_executed: 2,
      data: [
        {
          status: "ok",
          data: {
            _meta: { tool: "list_sessions", item_count: 21, shaped: true },
          },
        },
      ],
    })
    expect(text).toBe("Completed list_sessions — 21 item(s).")
  })
})
