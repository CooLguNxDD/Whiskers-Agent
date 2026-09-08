/**
 * GraphCanvas Accessibility Unit Tests
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { GraphCanvas } from "../GraphCanvas"

describe("GraphCanvas Keyboard Accessibility", () => {
  it("renders nodes with role=button and tabIndex=0, and handles Space/Enter key presses", () => {
    const onSelectNode = vi.fn()
    const nodes = {}

    render(
      <GraphCanvas
        nodes={nodes}
        selectedNode={null}
        onSelectNode={onSelectNode}
      />
    )

    // Query node boxes via role="button" with graph-node class
    const buttons = screen.getAllByRole("button").filter(b => b.classList.contains("graph-node"))
    expect(buttons.length).toBeGreaterThan(0)

    const firstButton = buttons[0]
    expect(firstButton).toHaveAttribute("tabindex", "0")
    expect(firstButton).toHaveAttribute("aria-label")

    // Dispatch real Enter keyboard event to activate selection
    const enterEvent = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    })
    firstButton.dispatchEvent(enterEvent)
    expect(onSelectNode).toHaveBeenCalledTimes(1)

    // Dispatch real Space keyboard event to activate selection and prevent default
    const spaceEvent = new KeyboardEvent("keydown", {
      key: " ",
      bubbles: true,
      cancelable: true,
    })
    const preventDefaultSpy = vi.spyOn(spaceEvent, "preventDefault")
    firstButton.dispatchEvent(spaceEvent)
    expect(onSelectNode).toHaveBeenCalledTimes(2)
    expect(preventDefaultSpy).toHaveBeenCalled()
  })
})
