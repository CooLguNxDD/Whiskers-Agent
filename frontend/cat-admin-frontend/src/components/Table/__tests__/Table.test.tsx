import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { Table } from "../Table"

interface TestData {
  id: string
  name: string
  age: number
}

describe("Table Component", () => {
  const columns = [
    {
      key: "name",
      header: "Name",
      sortable: true,
      sortValue: (item: TestData) => item.name,
    },
    {
      key: "age",
      header: "Age",
      sortable: true,
      sortValue: (item: TestData) => item.age,
    },
  ]

  const rows: TestData[] = [
    { id: "1", name: "Charlie", age: 30 },
    { id: "2", name: "Alice", age: 25 },
    { id: "3", name: "Bob", age: 35 },
  ]

  it("renders columns and rows correctly", () => {
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
      />
    )

    // Verify headers
    expect(screen.getByText("Name")).toBeInTheDocument()
    expect(screen.getByText("Age")).toBeInTheDocument()

    // Verify rows
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Bob")).toBeInTheDocument()
    expect(screen.getByText("Charlie")).toBeInTheDocument()
  })

  it("displays empty message when rows are empty", () => {
    render(
      <Table
        columns={columns}
        rows={[]}
        rowKey={(r) => r.id}
        emptyMessage="No items found"
      />
    )

    expect(screen.getByText("No items found")).toBeInTheDocument()
  })

  it("handles row clicks", () => {
    const handleRowClick = vi.fn()
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowClick={handleRowClick}
      />
    )

    fireEvent.click(screen.getByText("Alice"))
    expect(handleRowClick).toHaveBeenCalledWith(rows[1])
  })

  it("sorts rows when clicking header", () => {
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
      />
    )

    // Initial render displays: Charlie (30), Alice (25), Bob (35)
    // Click Name header to sort Ascending: Alice, Bob, Charlie
    fireEvent.click(screen.getByText("Name"))
    
    const renderedNamesAfterFirstClick = screen
      .getAllByText(/Alice|Bob|Charlie/)
      .map((el) => el.textContent)
    
    expect(renderedNamesAfterFirstClick[0]).toBe("Alice")
    expect(renderedNamesAfterFirstClick[1]).toBe("Bob")
    expect(renderedNamesAfterFirstClick[2]).toBe("Charlie")

    // Click Name header again to sort Descending: Charlie, Bob, Alice
    fireEvent.click(screen.getByText("Name"))
    
    const renderedNamesAfterSecondClick = screen
      .getAllByText(/Alice|Bob|Charlie/)
      .map((el) => el.textContent)
    
    expect(renderedNamesAfterSecondClick[0]).toBe("Charlie")
    expect(renderedNamesAfterSecondClick[1]).toBe("Bob")
    expect(renderedNamesAfterSecondClick[2]).toBe("Alice")
  })

  it("supports keyboard navigation for headers and rows", () => {
    const handleRowClick = vi.fn()
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowClick={handleRowClick}
      />
    )

    const nameHeader = screen.getByText("Name").closest("[role='columnheader']")!
    expect(nameHeader).toHaveAttribute("tabindex", "0")
    expect(nameHeader).toHaveAttribute("role", "columnheader")

    // Press Enter to sort
    fireEvent.keyDown(nameHeader, { key: "Enter", code: "Enter" })

    const sortedNames = screen.getAllByText(/Alice|Bob|Charlie/).map(el => el.textContent)
    expect(sortedNames[0]).toBe("Alice")

    // Find row
    const aliceRowDivs = screen.getAllByText("Alice")
    // In our component, "Alice" is inside a div, which is inside the row div.
    // The row div is the one with tabindex=0
    const aliceRow = aliceRowDivs[0].closest("[role='row']")!
    expect(aliceRow).toHaveAttribute("tabindex", "0")

    // Press Space to select
    fireEvent.keyDown(aliceRow, { key: " ", code: "Space" })
    expect(handleRowClick).toHaveBeenCalledWith(rows[1])
  })
})
