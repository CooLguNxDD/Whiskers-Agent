import { render, screen } from "@testing-library/react"
import { describe, it, expect } from "vitest"
import { FormattedMarkdown, parseInline } from "../FormattedMarkdown"

describe("FormattedMarkdown", () => {
  it("parses inline bold, italic, and code formatting", () => {
    const nodes = parseInline("Hello **world** and *stars* with `code`")
    expect(nodes.length).toBe(6)
  })

  it("renders structured markdown headings, lists, and line breaks", () => {
    const content = `## Overview
In *Blue Archive*, the Tea Party is the governing student council.

## Comparison of Candidates
- **Misono Mika (Leader of Pater):** Fan favorite
- **Kirifuji Nagisa (Leader of Filius):** Special Attacker

## Reasoned Lean
While "best girl" is subjective, **Mika Misono** is favored.`

    const { container } = render(<FormattedMarkdown content={content} />)

    expect(screen.getByText("Overview")).toBeInTheDocument()
    expect(screen.getByText("Comparison of Candidates")).toBeInTheDocument()
    expect(screen.getByText("Reasoned Lean")).toBeInTheDocument()
    expect(screen.getByText("Misono Mika (Leader of Pater):")).toBeInTheDocument()
    expect(screen.getByText("Kirifuji Nagisa (Leader of Filius):")).toBeInTheDocument()
    expect(screen.getByText("Mika Misono")).toBeInTheDocument()
    expect(container.querySelectorAll("li").length).toBe(2)
    const headings = container.querySelectorAll('[role="heading"]')
    expect(headings.length).toBe(3)
    expect(headings[0].getAttribute("aria-level")).toBe("2")
  })
})
