import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createElement, type ReactNode } from "react"

import { useCatalogQuery } from "@/hooks/useCatalog"
import { getCatalog, type CatalogOperation, type CatalogResponse } from "@/api/catalog"
import {
  getCatalogOperations,
  invalidateCatalogClient,
  setCatalogSnapshot,
} from "@/api/catalogRuntime"
import { useSessionStore } from "@/store"

vi.mock("@/api/catalog", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/api/catalog")>()
  return {
    ...mod,
    getCatalog: vi.fn(),
  }
})

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
  return { queryClient, wrapper }
}

const sampleOp = (over: Partial<CatalogOperation> = {}): CatalogOperation => ({
  plugin_id: "p1",
  operation_id: "p1__echo",
  description: "echo",
  input_schema: { type: "object", properties: {} },
  access: "read",
  required_scopes: [],
  visibility: "authenticated",
  version: "1",
  tags: ["p1"],
  http: null,
  mcp: { tool_name: "echo" },
  ui: null,
  is_fast_path: true,
  descriptor_hash: "abc",
  ...over,
})

const snap = (
  operations: CatalogOperation[],
  over: Partial<CatalogResponse> = {},
): CatalogResponse => ({
  revision: 1,
  etag: '"v1"',
  operations,
  ...over,
})

describe("useCatalogQuery", () => {
  beforeEach(() => {
    vi.mocked(getCatalog).mockReset()
    invalidateCatalogClient()
    useSessionStore.setState({
      status: "connected",
      authProbeDone: true,
    })
  })

  it("throws on first-load 304 instead of caching an empty catalog", async () => {
    vi.mocked(getCatalog).mockResolvedValue(null)
    const { wrapper } = createWrapper()
    const { result } = renderHook(() => useCatalogQuery(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
    expect(result.current.error?.message).toMatch(/304 with no prior data/)
    expect(getCatalogOperations()).toHaveLength(0)
  })

  it("reuses this query's prior data on a refetch 304", async () => {
    const first = snap([sampleOp()])
    vi.mocked(getCatalog).mockResolvedValueOnce(first)
    const { wrapper } = createWrapper()
    const { result } = renderHook(() => useCatalogQuery(), { wrapper })

    await waitFor(() => expect(result.current.data?.operations).toHaveLength(1))
    expect(getCatalogOperations()).toHaveLength(1)

    vi.mocked(getCatalog).mockResolvedValueOnce(null)
    await result.current.refetch()

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(first)
    expect(getCatalogOperations()).toHaveLength(1)
  })

  it("does not inject the unfiltered runtime snapshot into a pluginId query on 304", async () => {
    setCatalogSnapshot(
      snap([
        sampleOp(),
        sampleOp({ plugin_id: "p2", operation_id: "p2__other", descriptor_hash: "def" }),
      ]),
    )
    vi.mocked(getCatalog).mockResolvedValue(null)
    const { wrapper } = createWrapper()
    const { result } = renderHook(() => useCatalogQuery({ pluginId: "p1" }), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
    expect(getCatalogOperations()).toHaveLength(2)
  })

  it("pluginId refetch 304 keeps the filtered query data, not the unfiltered snapshot", async () => {
    const filtered = snap([sampleOp()], { etag: '"p1"' })
    vi.mocked(getCatalog).mockResolvedValueOnce(filtered)
    const { wrapper } = createWrapper()
    const { result } = renderHook(() => useCatalogQuery({ pluginId: "p1" }), { wrapper })

    await waitFor(() => expect(result.current.data?.operations).toHaveLength(1))

    setCatalogSnapshot(
      snap([
        sampleOp(),
        sampleOp({ plugin_id: "p2", operation_id: "p2__other", descriptor_hash: "def" }),
      ]),
    )
    vi.mocked(getCatalog).mockResolvedValueOnce(null)
    await result.current.refetch()

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.operations).toHaveLength(1)
    expect(result.current.data?.operations[0]?.plugin_id).toBe("p1")
    expect(result.current.data?.etag).toBe('"p1"')
  })

  it("seeds the module snapshot only from an unfiltered 200", async () => {
    vi.mocked(getCatalog).mockResolvedValueOnce(
      snap([sampleOp({ plugin_id: "p1" })], { etag: '"p1"' }),
    )
    const { wrapper } = createWrapper()
    const { result } = renderHook(() => useCatalogQuery({ pluginId: "p1" }), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(getCatalogOperations()).toHaveLength(0)

    vi.mocked(getCatalog).mockResolvedValueOnce(snap([sampleOp()], { etag: '"all"' }))
    const full = renderHook(() => useCatalogQuery(), { wrapper })
    await waitFor(() => expect(full.result.current.isSuccess).toBe(true))
    expect(getCatalogOperations()).toHaveLength(1)
  })
})
