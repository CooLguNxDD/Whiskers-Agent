import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { GlobalScopesPanel } from "@/components/ApiKeys"
import { createElement } from "react"
import "@testing-library/jest-dom"

// useScopeEditor tests moved to ./useScopeEditor.test.ts (hook moved to hooks/useScopeEditor.ts)

describe("GlobalScopesPanel Component", () => {
  it("sources global-scope ids from a passed-in scopes prop and only renders those, with no defaults bleeding through", () => {
    const mockToggle = vi.fn()
    
    // Render the panel with only "whiskers" and "agy"
    render(
      createElement(GlobalScopesPanel, {
        selectedScopes: [],
        onToggleScope: mockToggle,
        scopes: ["whiskers", "agy"],
      })
    )

    // Assert that "whiskers" and "agy" are rendered
    expect(screen.getByText("whiskers")).toBeInTheDocument()
    expect(screen.getByText("agy")).toBeInTheDocument()

    // Assert that "terminal:use", "terminal:host", "sandbox:exec" are NOT rendered
    expect(screen.queryByText("terminal:use")).not.toBeInTheDocument()
    expect(screen.queryByText("terminal:host")).not.toBeInTheDocument()
    expect(screen.queryByText("sandbox:exec")).not.toBeInTheDocument()
  })
})
