/**
 * SchemaForm baseline renderer tests.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { SchemaForm } from "../SchemaForm"

describe("SchemaForm", () => {
  it("renders titled fields and submits values", async () => {
    const onSubmit = vi.fn()
    render(
      <SchemaForm
        schema={{
          type: "object",
          properties: {
            msg: { type: "string", title: "Message", default: "hello" },
            mode: { type: "string", enum: ["a", "b"], title: "Mode" },
          },
          required: ["msg"],
          "x-whiskers-ui": { order: ["mode", "msg"] },
        }}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.getByLabelText(/Message/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Mode/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/Message/), {
      target: { value: "hi" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Run/i }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled()
    })
    const arg = onSubmit.mock.calls[0][0] as Record<string, unknown>
    expect(arg.msg).toBe("hi")
  })

  it("uses password input for secret fields", () => {
    render(
      <SchemaForm
        schema={{
          type: "object",
          properties: {
            token: {
              type: "string",
              title: "Token",
              "x-whiskers-ui": { secret: true },
            },
          },
        }}
        onSubmit={() => undefined}
      />,
    )
    expect(screen.getByLabelText(/Token/)).toHaveAttribute("type", "password")
  })

  it("loads async options into select after loadAsyncOptions resolves", async () => {
    const loadAsyncOptions = vi.fn().mockResolvedValue([
      { value: "1", label: "Alpha" },
      { value: "2", label: "Beta" },
    ])

    render(
      <SchemaForm
        schema={{
          type: "object",
          properties: {
            choice: {
              type: "string",
              title: "Choice",
              "x-whiskers-ui": {
                asyncOptions: {
                  plugin_id: "p1",
                  operation_id: "p1__list_choices",
                  valueKey: "id",
                  labelKey: "name",
                  args: {},
                },
              },
            },
          },
        }}
        loadAsyncOptions={loadAsyncOptions}
        onSubmit={() => undefined}
      />,
    )

    expect(screen.getByLabelText(/Choice/)).toBeInTheDocument()
    expect(loadAsyncOptions).toHaveBeenCalledWith({
      plugin_id: "p1",
      operation_id: "p1__list_choices",
      valueKey: "id",
      labelKey: "name",
      args: {},
    })

    await waitFor(() => {
      expect(screen.getByRole("option", { name: "Alpha" })).toBeInTheDocument()
      expect(screen.getByRole("option", { name: "Beta" })).toBeInTheDocument()
    })
  })

  it("shows hint when asyncOptions present but loadAsyncOptions is missing", () => {
    render(
      <SchemaForm
        schema={{
          type: "object",
          properties: {
            choice: {
              type: "string",
              title: "Choice",
              "x-whiskers-ui": {
                asyncOptions: {
                  plugin_id: "p1",
                  operation_id: "p1__list_choices",
                  valueKey: "id",
                  labelKey: "name",
                },
              },
            },
          },
        }}
        onSubmit={() => undefined}
      />,
    )

    expect(screen.getByLabelText(/Choice/)).toBeInTheDocument()
    expect(
      screen.getByText(/Async options unavailable — provide loadAsyncOptions/i),
    ).toBeInTheDocument()
  })

  it("keeps invalid free-form JSON text without flickering", () => {
    render(
      <SchemaForm
        schema={{ type: "object", properties: {} }}
        onSubmit={() => undefined}
      />,
    )
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: "{ not valid" } })
    expect(ta.value).toBe("{ not valid")
    expect(screen.getByText(/Invalid JSON/i)).toBeInTheDocument()
  })

  it("resets field values when schema defaults change", async () => {
    const { rerender } = render(
      <SchemaForm
        schema={{
          type: "object",
          properties: {
            msg: { type: "string", title: "Message", default: "one" },
          },
        }}
        onSubmit={() => undefined}
      />,
    )
    const input = screen.getByLabelText(/Message/) as HTMLInputElement
    expect(input.value).toBe("one")
    fireEvent.change(input, { target: { value: "edited" } })
    expect(input.value).toBe("edited")

    rerender(
      <SchemaForm
        schema={{
          type: "object",
          properties: {
            msg: { type: "string", title: "Message", default: "two" },
          },
        }}
        onSubmit={() => undefined}
      />,
    )
    await waitFor(() => {
      expect((screen.getByLabelText(/Message/) as HTMLInputElement).value).toBe(
        "two",
      )
    })
  })
})
