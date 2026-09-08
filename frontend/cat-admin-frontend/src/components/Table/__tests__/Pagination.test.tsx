import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { Pagination } from "../Pagination"

describe("Pagination Component", () => {
  it("renders pagination controls correctly", () => {
    const handlePageChange = vi.fn()
    const handlePerPageChange = vi.fn()

    render(
      <Pagination
        total={35}
        page={2}
        perPage={10}
        onPageChange={handlePageChange}
        onPerPageChange={handlePerPageChange}
      />
    )

    // Verify row range info
    expect(screen.getByText(/showing/)).toBeInTheDocument()
    expect(screen.getByText("11–20")).toBeInTheDocument()
    expect(screen.getByText("35")).toBeInTheDocument()

    // Verify page numbers are rendered
    expect(screen.getByText("1")).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.getByText("4")).toBeInTheDocument()

    // Verify prev and next buttons
    expect(screen.getByText("← prev")).toBeInTheDocument()
    expect(screen.getByText(/next/)).toBeInTheDocument()
  })

  it("handles page changes click", () => {
    const handlePageChange = vi.fn()
    render(
      <Pagination
        total={35}
        page={2}
        perPage={10}
        onPageChange={handlePageChange}
        onPerPageChange={vi.fn()}
      />
    )

    // Click prev page
    fireEvent.click(screen.getByText("← prev"))
    expect(handlePageChange).toHaveBeenCalledWith(1)

    // Click page 3
    fireEvent.click(screen.getByText("3"))
    expect(handlePageChange).toHaveBeenCalledWith(3)
  })

  it("disables prev button on first page and next button on last page", () => {
    const { rerender } = render(
      <Pagination
        total={15}
        page={1}
        perPage={10}
        onPageChange={vi.fn()}
        onPerPageChange={vi.fn()}
      />
    )

    expect(screen.getByText("← prev")).toBeDisabled()
    expect(screen.getByText(/next/)).not.toBeDisabled()

    // Rerender on page 2 (last page since total = 15, perPage = 10)
    rerender(
      <Pagination
        total={15}
        page={2}
        perPage={10}
        onPageChange={vi.fn()}
        onPerPageChange={vi.fn()}
      />
    )

    expect(screen.getByText("← prev")).not.toBeDisabled()
    expect(screen.getByText(/next/)).toBeDisabled()
  })

  it("handles perPage change option clicks", () => {
    const handlePerPageChange = vi.fn()
    render(
      <Pagination
        total={35}
        page={2}
        perPage={10}
        onPageChange={vi.fn()}
        onPerPageChange={handlePerPageChange}
      />
    )

    // Click 25 rows option
    fireEvent.click(screen.getByText("25"))
    expect(handlePerPageChange).toHaveBeenCalledWith(25)
  })
})
