import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

describe("client.ts", () => {
  let originalFetch: typeof globalThis.fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
    vi.clearAllMocks()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  describe("tryRefreshSession", () => {
    it("resolves false when fetch rejects (e.g. timeout/abort)", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError"))
      // Need to reset refreshPromise between tests — re-import triggers fresh module state
      const { tryRefreshSession } = await import("../client?t=" + Date.now())
      const result = await tryRefreshSession()
      expect(result).toBe(false)
    })

    it("resolves true when refresh succeeds", async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: true } as Response)
      const { tryRefreshSession } = await import("../client?t=" + Date.now())
      const result = await tryRefreshSession()
      expect(result).toBe(true)
    })
  })

  describe("auth:expired de-duplication", () => {
    it("emits auth:expired exactly once for N concurrent 401s sharing one failed refresh", async () => {
      let resolveRefresh!: (v: Response) => void
      const refreshDeferred = new Promise<Response>((resolve) => {
        resolveRefresh = resolve
      })

      globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes("/admin/public/refresh")) return refreshDeferred
        return Promise.resolve({ status: 401, ok: false, statusText: "Unauthorized" } as Response)
      }) as unknown as typeof fetch

      const { request } = await import("../client?t=" + Date.now() + "dedupe")
      const { bus } = await import("../../events/bus")

      const handler = vi.fn()
      bus.on("auth:expired", handler)

      const calls = [
        request("/api/a").catch(() => undefined),
        request("/api/b").catch(() => undefined),
        request("/api/c").catch(() => undefined),
      ]

      // Let all three requests reach tryRefreshSession() and share the same
      // in-flight refresh promise before it resolves.
      await Promise.resolve()
      await Promise.resolve()
      resolveRefresh({ ok: false } as Response)

      await Promise.all(calls)

      expect(handler).toHaveBeenCalledTimes(1)
      bus.off("auth:expired", handler)
    })
  })

  describe("request<T> content-type guard", () => {
    it("throws meaningful error when response is non-JSON (e.g. 502 HTML)", async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => "text/html; charset=utf-8" },
        json: vi.fn(),
      } as unknown as Response)
      const { request } = await import("../client?t=" + Date.now() + "a")
      await expect(request("/api/test")).rejects.toThrow(/Expected JSON/)
    })

    it("throws meaningful error when JSON body is malformed", async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => "application/json" },
        json: vi.fn().mockRejectedValue(new SyntaxError("Unexpected token")),
      } as unknown as Response)
      const { request } = await import("../client?t=" + Date.now() + "c")
      await expect(request("/api/test")).rejects.toThrow(/Failed to parse JSON response/)
    })

    it("returns parsed data when content-type is application/json", async () => {
      const payload = { id: 1, name: "test" }
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => "application/json; charset=utf-8" },
        json: vi.fn().mockResolvedValue(payload),
      } as unknown as Response)
      const { request } = await import("../client?t=" + Date.now() + "b")
      const result = await request<typeof payload>("/api/test")
      expect(result).toEqual(payload)
    })

    it("throws meaningful error when JSON body is malformed", async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => "application/json" },
        json: vi.fn().mockRejectedValue(new SyntaxError("Unexpected token")),
      } as unknown as Response)
      const { request } = await import("../client?t=" + Date.now() + "c")
      await expect(request("/api/test")).rejects.toThrow(/Failed to parse JSON response/)
    })
  })
})
